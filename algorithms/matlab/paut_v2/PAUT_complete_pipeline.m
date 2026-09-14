function [report, image_result] = PAUT_complete_pipeline(input_path,output_dir_override,cfg_override)
%PAUT_COMPLETE_PIPELINE  PAUT二维FMC自动成像、检出、分类与最新定量流程。
%
%   report = PAUT_complete_pipeline('data\2\(0.045,0.02)h0.007-b0.0005,a60');
%
% 运行示例（嵌套FMC，data\tx_1\001.txt）：
%   report = PAUT_complete_pipeline('data5\pore_d1.2_z20_x37');
%
% 流程：原始FMC -> DAS -> 自动候选检测 -> 裂纹CNN -> 非裂纹形态判断
%      -> 裂纹/未熔合长度、气孔面积，或显式夹渣类型的大条状夹渣定量
%      -> CSV和标注图。
% 对非裂纹细长候选，LOF/Slag分类器给出保守三分类；高置信Slag
% 会晋级为最终Type，并输出夹渣长轴、短轴名义值/范围和名义面积。
%
% 当前限制：
%   1. 裂纹CNN使用data/data2/data5联合微调模型，判定阈值固定为0.98；
%   2. 气孔仍使用形态规则；细长非裂纹候选再由LOF/Slag分类器区分；
%   3. 自动候选检测采用固定全局阈值，弱小缺陷可能漏检；
%   4. 当前仅适用于本工程相同材料、探头、采样和二维仿真设置。

if nargin < 1 || isempty(input_path)
    error('请提供一个FMC案例文件夹或data3单文件。');
end
if nargin < 2, output_dir_override = ''; end
if nargin < 3 || isempty(cfg_override), cfg_override = struct(); end
% 不在可复用主函数中清屏或关闭用户图窗；批量验证程序会管理本次生成的图窗。

workspace_dir = fileparts(mfilename('fullpath'));
cfg = default_config(workspace_dir);
input_path = char(input_path);
if ~isfolder(input_path) && ~isfile(input_path)
    input_path = fullfile(workspace_dir,input_path);
end
if ~isfolder(input_path) && ~isfile(input_path)
    error('输入案例不存在: %s',input_path);
end

if isempty(output_dir_override)
    timestamp = datestr(now,'yyyymmdd_HHMMSS');
    cfg.output_dir = fullfile(workspace_dir,'auto_analysis_results',timestamp);
else
    cfg.output_dir = char(output_dir_override);
    if ~isfolder(fileparts(cfg.output_dir)) && ~isempty(fileparts(cfg.output_dir))
        mkdir(fileparts(cfg.output_dir));
    end
end
if ~isfolder(cfg.output_dir), mkdir(cfg.output_dir); end

fprintf('输入案例: %s\n',input_path);
[data_format,data_folder] = detect_input_format(input_path);
fprintf('识别数据格式: %s\n',data_format);
if strcmp(data_format,'fmc257')
    % data3沿用FMC_DAS_16x16的数据范围和早期时间门控。
    cfg.x_img = 20:0.2:70;
    cfg.gate_start_us = 0.1;
end
% 新的60~90度裂纹长度模型是在data\2\none公共背景扣除后训练的。
% 对data\2中的案例自动采用相同预处理；调用方仍可通过cfg_override将其置空。
data2_root = fullfile(workspace_dir,'data','2');
if is_path_under(input_path,data2_root)
    cfg.background_input_path = fullfile(data2_root,'none');
end
cfg = apply_config_override(cfg,cfg_override);
[data_cube,time_axis,tx_positions,rx_positions] = read_fmc( ...
    data_folder,data_format,cfg);
actual_sample_rate_hz = 1/median(diff(time_axis));
if ~isempty(cfg.expected_sample_rate_hz)
    relative_error = abs(actual_sample_rate_hz-cfg.expected_sample_rate_hz)/cfg.expected_sample_rate_hz;
    if relative_error > 0.01
        error('数据实际采样率 %.3f MHz 与设定采样率 %.3f MHz 不一致。', ...
            actual_sample_rate_hz/1e6,cfg.expected_sample_rate_hz/1e6);
    end
end
fprintf('实际采样率: %.3f MHz（已通过采集设置校验）\n',actual_sample_rate_hz/1e6);
if ~isempty(cfg.background_input_path)
    background_path = char(cfg.background_input_path);
    if ~isfolder(background_path) && ~isfile(background_path)
        background_path = fullfile(workspace_dir,background_path);
    end
    if ~isfolder(background_path) && ~isfile(background_path)
        error('公共背景数据不存在: %s',background_path);
    end
    [background_format,background_folder] = detect_input_format(background_path);
    [background_cube,background_time,background_tx,background_rx] = ...
        read_fmc(background_folder,background_format,cfg);
    [background_cube,background_time] = align_background_data( ...
        data_cube,time_axis,background_cube,background_time);
    validate_background_data(data_cube,time_axis,tx_positions,rx_positions, ...
        background_cube,background_time,background_tx,background_rx);
    data_cube = data_cube-cfg.background_scale*background_cube;
    fprintf('已扣除公共背景: %s（比例 %.3f）\n',background_path,cfg.background_scale);
end

noise_info = struct('enabled',false,'requested_snr_db',Inf, ...
    'achieved_snr_db',Inf,'signal_rms',NaN,'noise_sigma',0, ...
    'noise_rms',0,'seed',double(cfg.gaussian_noise_seed));
if cfg.enable_gaussian_noise
    [data_cube,noise_details] = add_gaussian_noise(data_cube, ...
        cfg.gaussian_noise_snr_db,cfg.gaussian_noise_seed);
    noise_info = noise_details;
    noise_info.enabled = true;
    fprintf('已添加高斯白噪声：目标SNR %.2f dB，实际SNR %.2f dB，种子 %d。\n', ...
        noise_info.requested_snr_db,noise_info.achieved_snr_db,noise_info.seed);
end

fprintf('开始DAS成像...\n');
[envelope,img_db,x_img,z_img] = reconstruct_das( ...
    data_cube,time_axis,tx_positions,rx_positions,cfg);
fprintf('DAS完成，网格 %d x %d。\n',numel(z_img),numel(x_img));

candidates = detect_candidates(envelope,x_img,z_img,cfg);
fprintf('自动检出候选区域: %d 个。\n',numel(candidates));
if isempty(candidates)
    report = empty_report();
    image_result = struct('envelope',envelope,'img_db',img_db, ...
        'x_img',x_img,'z_img',z_img,'candidates',candidates, ...
        'noise_info',noise_info,'sample_rate_hz',actual_sample_rate_hz, ...
        'output_dir',cfg.output_dir);
    save_outputs(report,image_result,input_path,cfg,[]);
    warning('没有检出满足当前阈值的候选区域。');
    return;
end

patch_path = fullfile(cfg.output_dir,'candidate_patches.mat');
json_path = fullfile(cfg.output_dir,'crack_inference.json');
force_slag_requested = strcmpi(strtrim(char(string(cfg.forced_defect_type))),'Slag');
if force_slag_requested
    % 类型由经验证的上游分类器或操作者明确提供时，不运行无关的裂纹CNN。
    % NaN明确表示“未评估”，不能解释为低裂纹概率。
    inference = struct('probabilities',nan(numel(candidates),1), ...
        'predictions',zeros(numel(candidates),1),'threshold',NaN);
else
    % 为裂纹CNN截取固定16 mm物理窗口。
    patches_db = zeros(cfg.patch_pixels,cfg.patch_pixels,numel(candidates),'single');
    for k = 1:numel(candidates)
        patches_db(:,:,k) = extract_patch(img_db,x_img,z_img, ...
            candidates(k).center_x_mm,candidates(k).center_z_mm,cfg);
    end
    save(patch_path,'patches_db','-v7');
    inference = run_crack_inference(patch_path,json_path,cfg);
    % 检查点中的阈值受小验证集影响偏高。完整流程使用独立验证后选定的0.98，
    % 优先保证裂纹检出及后续长度定量。
    inference.threshold = cfg.crack_probability_threshold;
    inference.predictions = double(double(inference.probabilities(:)) >= ...
        cfg.crack_probability_threshold);
end

if force_slag_requested
    % 显式夹渣路径只加载自身模型，避免无关模型缺失影响夹渣定量。
    crack_calibration = struct();
    refined_crack_model = struct('available',false);
    lof_calibration = struct();
    pore_calibration = struct();
else
    crack_calibration = load_calibration(cfg.crack_length_model,'裂纹长度');
    refined_crack_model = load_refined_crack_model(cfg.refined_crack_model);
    lof_calibration = load_calibration(cfg.lof_length_model,'未熔合长度');
    pore_calibration = load_pore_calibration(cfg.pore_area_model);
end
slag_calibration = load_slag_calibration(cfg.slag_area_model);
lof_slag_advisor = load_lof_slag_advisor(cfg.lof_slag_classifier_model);

n = numel(candidates);
defect_id = (1:n)';
type = cell(n,1);
classification_method = cell(n,1);
lof_slag_suggestion = repmat({'NotEvaluated'},n,1);
lof_slag_suggestion_status = repmat({'Not eligible or model unavailable'},n,1);
slag_probability = nan(n,1);
advisory_slag_major = nan(n,1);
advisory_slag_short_lower = nan(n,1); advisory_slag_short_upper = nan(n,1);
advisory_slag_area = nan(n,1);
advisory_slag_area_lower = nan(n,1); advisory_slag_area_upper = nan(n,1);
center_x = zeros(n,1); center_z = zeros(n,1);
peak_x = zeros(n,1); peak_z = zeros(n,1);
crack_probability = double(inference.probabilities(:));
cnn_threshold = repmat(double(inference.threshold),n,1);
raw_length = nan(n,1); corrected_length = nan(n,1);
diameter = nan(n,1); area = nan(n,1);
minor_axis_nominal = nan(n,1); minor_axis_lower = nan(n,1);
minor_axis_upper = nan(n,1); area_lower = nan(n,1); area_upper = nan(n,1);
angle = nan(n,1); aspect_ratio = nan(n,1); snr_db = nan(n,1);
global_peak_db = nan(n,1); status = cell(n,1); warning_text = cell(n,1);
measurements = cell(n,1);

for k = 1:n
    candidate = candidates(k);
    measurement = measure_shape(envelope,x_img,z_img, ...
        candidate.center_x_mm,candidate.center_z_mm, ...
        candidate.peak_x_mm,candidate.peak_z_mm,cfg);
    measurements{k} = measurement;
    center_x(k) = measurement.centroid_x_mm;
    center_z(k) = measurement.centroid_z_mm;
    peak_x(k) = measurement.peak_x_mm;
    peak_z(k) = measurement.peak_z_mm;
    raw_length(k) = measurement.major_length_mm;
    angle(k) = measurement.image_angle_deg;
    aspect_ratio(k) = measurement.aspect_ratio;
    snr_db(k) = measurement.snr_db;
    global_peak_db(k) = candidate.global_peak_db;
    messages = {};
    cnn_predicts_crack = inference.predictions(k) == 1;
    coarse_model_angle = 90-abs(measurement.image_angle_deg);
    % 对CNN判为裂纹、但外形近圆的候选先做一次孔径试算。真实极短裂纹和
    % 小气孔的长宽比会重叠；孔模型是否落在有效范围且未触碰硬边界，提供
    % 一条独立于CNN/长宽比的幅值证据。
    pore_trial = struct();
    pore_trial_available = false;
    pore_response_supports_pore = false;
    if cnn_predicts_crack && measurement.aspect_ratio < cfg.crack_min_aspect_ratio
        try
            pore_trial = quantify_pore(envelope,x_img,z_img, ...
                measurement.peak_x_mm,measurement.peak_z_mm,pore_calibration);
            pore_trial_available = true;
            trial_bounded = isfield(pore_trial,'was_bounded') && pore_trial.was_bounded;
            pore_response_supports_pore = ~trial_bounded && ...
                pore_trial.diameter_mm >= pore_calibration.valid_diameter_range_mm(1)- ...
                    cfg.pore_response_range_tolerance_mm && ...
                pore_trial.diameter_mm <= pore_calibration.valid_diameter_range_mm(2)+ ...
                    cfg.pore_response_range_tolerance_mm;
        catch
            % 孔模型不可用时退回原有CNN概率与形态门控。
        end
    end
    short_crack_round_exemption = cnn_predicts_crack && ...
        crack_probability(k) >= cfg.short_crack_guard_probability && ...
        measurement.major_length_mm >= cfg.short_crack_guard_raw_length_range_mm(1) && ...
        measurement.major_length_mm <= cfg.short_crack_guard_raw_length_range_mm(2) && ...
        coarse_model_angle >= cfg.short_crack_guard_angle_range_deg(1) && ...
        coarse_model_angle <= cfg.short_crack_guard_angle_range_deg(2);
    round_shape_borderline = cnn_predicts_crack && ...
        crack_probability(k) >= cfg.round_shape_guard_probability && ...
        measurement.aspect_ratio >= cfg.round_shape_guard_min_aspect_ratio && ...
        measurement.aspect_ratio < cfg.crack_min_aspect_ratio;
    round_shape_override = cnn_predicts_crack && ...
        measurement.aspect_ratio < cfg.crack_min_aspect_ratio && ...
        ((pore_trial_available && pore_response_supports_pore) || ...
        (~pore_trial_available && ~round_shape_borderline && ~short_crack_round_exemption));

    force_slag = force_slag_requested;

    auto_slag = false;
    auto_slag_measurement = struct();
    auto_slag_metrics = struct();
    advisor_eligible = ~force_slag && lof_slag_advisor.available && ...
        ~cnn_predicts_crack && ...
        measurement.aspect_ratio >= cfg.lof_min_aspect_ratio && ...
        measurement.major_length_mm >= cfg.lof_min_length_mm;
    if advisor_eligible
        if measurement.centroid_z_mm < lof_slag_advisor.model.valid_depth_range_mm(1) || ...
                measurement.centroid_z_mm > lof_slag_advisor.model.valid_depth_range_mm(2)
            lof_slag_suggestion{k}='Indeterminate';
            lof_slag_suggestion_status{k}='Outside advisor depth domain';
        else
            advisor_feature=extract_lof_slag_advisor_features(envelope,x_img,z_img, ...
                measurement.peak_x_mm,measurement.peak_z_mm);
            advisor_result=predict_lof_slag_v1(lof_slag_advisor.model,advisor_feature);
            slag_probability(k)=advisor_result.SlagProbability(1);
            lof_slag_suggestion{k}=char(advisor_result.Decision(1));
            if advisor_result.Decision(1)=="Indeterminate"
                lof_slag_suggestion_status{k}='Probability in abstention interval';
            else
                lof_slag_suggestion_status{k}='High-confidence advisory';
                if advisor_result.Decision(1)=="Slag" && ...
                        cfg.enable_advisory_slag_quantification
                    shadow_cfg=cfg;
                    shadow_cfg.measure_threshold_db=slag_calibration.measure_threshold_db;
                    shadow_cfg.measure_roi_half_width_x_mm= ...
                        slag_calibration.measure_roi_half_width_x_mm;
                    shadow_cfg.measure_roi_half_width_z_mm= ...
                        slag_calibration.measure_roi_half_width_z_mm;
                    shadow_measurement=measure_shape(envelope,x_img,z_img, ...
                        candidate.center_x_mm,candidate.center_z_mm, ...
                        candidate.peak_x_mm,candidate.peak_z_mm,shadow_cfg);
                    if shadow_measurement.peak_x_mm<slag_calibration.bevel_x_threshold_mm
                        shadow_position="bevel";
                    else
                        shadow_position="layer";
                    end
                    shadow_metrics=predict_slag_large_v84(slag_calibration, ...
                        shadow_measurement.major_length_mm, ...
                        shadow_measurement.centroid_z_mm,shadow_position);
                    auto_slag=true;
                    auto_slag_measurement=shadow_measurement;
                    auto_slag_metrics=shadow_metrics;
                    messages{end+1}=sprintf( ...
                        'LOF/Slag分类器高概率判定夹渣（Slag概率%.3f），已切换夹渣定量模型', ...
                        slag_probability(k)); %#ok<AGROW>
                    if shadow_metrics.is_supported
                        advisory_slag_major(k)=shadow_metrics.major_axis_mm;
                        advisory_slag_short_lower(k)=shadow_metrics.short_axis_range_mm(1);
                        advisory_slag_short_upper(k)=shadow_metrics.short_axis_range_mm(2);
                        advisory_slag_area(k)=shadow_metrics.area_nominal_mm2;
                        advisory_slag_area_lower(k)=shadow_metrics.area_range_mm2(1);
                        advisory_slag_area_upper(k)=shadow_metrics.area_range_mm2(2);
                        lof_slag_suggestion_status{k}= ...
                            'High-confidence Slag; promoted with quantification';
                    else
                        lof_slag_suggestion_status{k}= ...
                            'High-confidence Slag; outside slag quantification domain';
                    end
                else
                    messages{end+1}=sprintf( ...
                        'LOF/Slag分类器高概率建议%s（Slag概率%.3f）', ...
                        char(advisor_result.Decision(1)),slag_probability(k)); %#ok<AGROW>
                end
            end
        end
    elseif force_slag
        lof_slag_suggestion{k}='Slag';
        lof_slag_suggestion_status{k}='Explicit type; advisor not evaluated';
    end

    if force_slag || auto_slag
        type{k} = 'Slag';
        if force_slag
            classification_method{k} = 'Explicit upstream/operator slag type';
            slag_cfg=cfg;
            slag_cfg.measure_threshold_db=slag_calibration.measure_threshold_db;
            slag_cfg.measure_roi_half_width_x_mm=slag_calibration.measure_roi_half_width_x_mm;
            slag_cfg.measure_roi_half_width_z_mm=slag_calibration.measure_roi_half_width_z_mm;
            measurement=measure_shape(envelope,x_img,z_img, ...
                candidate.center_x_mm,candidate.center_z_mm, ...
                candidate.peak_x_mm,candidate.peak_z_mm,slag_cfg);
        else
            classification_method{k} = 'High-confidence LOF/Slag classifier';
            measurement=auto_slag_measurement;
        end
        measurements{k}=measurement;
        center_x(k)=measurement.centroid_x_mm; center_z(k)=measurement.centroid_z_mm;
        peak_x(k)=measurement.peak_x_mm; peak_z(k)=measurement.peak_z_mm;
        raw_length(k)=measurement.major_length_mm; angle(k)=measurement.image_angle_deg;
        aspect_ratio(k)=measurement.aspect_ratio; snr_db(k)=measurement.snr_db;
        if auto_slag
            slag_metrics=auto_slag_metrics;
        else
            if peak_x(k)<slag_calibration.bevel_x_threshold_mm
                slag_position="bevel";
            else
                slag_position="layer";
            end
            slag_metrics=predict_slag_large_v84(slag_calibration,raw_length(k), ...
                center_z(k),slag_position);
        end
        corrected_length(k)=slag_metrics.major_axis_mm;
        minor_axis_nominal(k)=slag_metrics.short_axis_nominal_mm;
        minor_axis_lower(k)=slag_metrics.short_axis_range_mm(1);
        minor_axis_upper(k)=slag_metrics.short_axis_range_mm(2);
        if slag_metrics.is_supported
            area(k)=slag_metrics.area_nominal_mm2;
            area_lower(k)=slag_metrics.area_range_mm2(1);
            area_upper(k)=slag_metrics.area_range_mm2(2);
            status{k}='Slag large-strip major calibrated; minor interval estimated';
            messages{end+1}=sprintf('短轴为经验范围%.2f至%.2f mm，不是正式置信区间', ...
                minor_axis_lower(k),minor_axis_upper(k)); %#ok<AGROW>
        else
            status{k}='Slag detected; outside validated quantitative domain';
            messages{end+1}='夹渣超出大面积模型范围，未输出面积'; %#ok<AGROW>
            area(k)=NaN; area_lower(k)=NaN; area_upper(k)=NaN;
            if ~slag_metrics.in_valid_depth_range
                messages{end+1}='夹渣深度超出15至28 mm范围；3 mm不支持'; %#ok<AGROW>
            end
            if ~slag_metrics.in_valid_major_range
                messages{end+1}='预测长轴超出训练样本模型输出范围'; %#ok<AGROW>
            end
        end
    elseif cnn_predicts_crack && ~round_shape_override
        type{k} = 'Crack';
        if short_crack_round_exemption && ...
                measurement.aspect_ratio < cfg.crack_min_aspect_ratio
            classification_method{k} = 'CNN + high-confidence short-crack exemption';
            messages{end+1} = sprintf( ...
                '高概率短裂纹免除圆形覆盖（概率%.4f，粗测长度%.2f mm，长宽比%.2f）', ...
                crack_probability(k),measurement.major_length_mm, ...
                measurement.aspect_ratio); %#ok<AGROW>
        elseif round_shape_borderline
            classification_method{k} = 'CNN + guarded shape boundary';
            messages{end+1} = sprintf( ...
                '高CNN概率保留边界裂纹（长宽比%.2f位于%.2f至%.2f）', ...
                measurement.aspect_ratio,cfg.round_shape_guard_min_aspect_ratio, ...
                cfg.crack_min_aspect_ratio); %#ok<AGROW>
        else
            classification_method{k} = 'CNN';
        end
        % 裂纹位置采用PCA主轴两端点的几何中点，避免强回波质心向一端偏移。
        center_x(k) = measurement.pca_midpoint_x_mm;
        center_z(k) = measurement.pca_midpoint_z_mm;
        estimated_model_angle = 90-abs(measurement.image_angle_deg);
        coarse_raw_length = raw_length(k);
        old_model_available = isfield(crack_calibration,'intercept_mm') && ...
            isfield(crack_calibration,'slope');
        old_corrected_length = NaN;
        if old_model_available
            old_corrected_length = crack_calibration.intercept_mm + ...
                crack_calibration.slope*coarse_raw_length;
        end
        refined_used = false;
        short_angle_tolerance = crack_probability(k)>=cfg.short_refined_angle_probability && ...
            coarse_raw_length>=cfg.short_refined_angle_length_range_mm(1) && ...
            coarse_raw_length<=cfg.short_refined_angle_length_range_mm(2) && ...
            estimated_model_angle>=cfg.short_refined_angle_tolerance_range_deg(1) && ...
            estimated_model_angle<cfg.refined_crack_execution_angle_range_deg(1);
        standard_refined_angle = ...
            estimated_model_angle >= cfg.refined_crack_execution_angle_range_deg(1) && ...
            estimated_model_angle <= cfg.refined_crack_execution_angle_range_deg(2);
        if cfg.enable_refined_crack_model && refined_crack_model.available && ...
                (standard_refined_angle || short_angle_tolerance)
            try
                coarse_gate_length = old_corrected_length;
                if ~isfinite(coarse_gate_length), coarse_gate_length=coarse_raw_length; end
                refined = quantify_refined_crack(data_cube,time_axis, ...
                    tx_positions,rx_positions,center_x(k),center_z(k), ...
                    coarse_raw_length,coarse_gate_length,cfg, ...
                    refined_crack_model);
                refined_angle_allowed = refined.available && ( ...
                    (refined.estimated_model_angle_deg>=cfg.refined_crack_execution_angle_range_deg(1) && ...
                     refined.estimated_model_angle_deg<=cfg.refined_crack_execution_angle_range_deg(2)) || ...
                    (short_angle_tolerance && ...
                     refined.estimated_model_angle_deg>=cfg.short_refined_angle_tolerance_range_deg(1)));
                if refined_angle_allowed
                    raw_length(k) = refined.length10_mm;
                    center_x(k) = refined.center_x_mm;
                    center_z(k) = refined.center_z_mm;
                    angle(k) = refined.image_angle_deg;
                    aspect_ratio(k) = refined.aspect_ratio;
                    snr_db(k) = refined.snr_db;
                    corrected_length(k) = refined.corrected_length_mm;
                    if refined.dual_branch_applied
                        status{k} = sprintf('Crack length: local refined dual branch (long weight %.2f)', ...
                            refined.long_branch_weight);
                    elseif refined.short_residual_applied
                        status{k} = 'Crack length: local refined + short residual model';
                    elseif short_angle_tolerance
                        status{k} = 'Crack length: local refined model (short-angle tolerance)';
                    else
                    status{k} = 'Crack length: local 0.05 mm refined model (45-90 deg)';
                    end
                    if refined.length_spread_mm > cfg.refined_length_spread_warning_mm
                        messages{end+1} = sprintf( ...
                            '多阈值长度离散较大（范围%.3f mm）', ...
                            refined.length_spread_mm); %#ok<AGROW>
                    end
                    refined_used = true;
                else
                    messages{end+1} = '局部精细角度不在执行范围，已回退旧模型'; %#ok<AGROW>
                end
            catch exception
                messages{end+1} = sprintf('局部精细定量失败，已回退旧模型：%s',exception.message); %#ok<AGROW>
            end
        end
        if ~refined_used && old_model_available
            corrected_length(k) = old_corrected_length;
            status{k} = 'Crack length calibrated';
            if isfield(crack_calibration,'valid_angle_range_deg') && ...
                    (estimated_model_angle < crack_calibration.valid_angle_range_deg(1)- ...
                    cfg.calibration_angle_tolerance_deg || ...
                    estimated_model_angle > crack_calibration.valid_angle_range_deg(2)+ ...
                    cfg.calibration_angle_tolerance_deg)
                messages{end+1} = '估计角度超出连续裂纹模型范围'; %#ok<AGROW>
            end
            if estimated_model_angle >= cfg.low_confidence_crack_angle_range_deg(1) && ...
                    estimated_model_angle <= cfg.low_confidence_crack_angle_range_deg(2)
                messages{end+1} = '45度附近裂纹长度为低可信结果'; %#ok<AGROW>
                status{k} = 'Crack length calibrated (low-confidence angle)';
            end
            if isfield(crack_calibration,'valid_length_range_mm') && ...
                    (corrected_length(k) < crack_calibration.valid_length_range_mm(1) || ...
                    corrected_length(k) > crack_calibration.valid_length_range_mm(2))
                messages{end+1} = '裂纹长度超出标定范围'; %#ok<AGROW>
            end
        elseif ~refined_used
            status{k} = 'Crack detected; length model unavailable';
            messages{end+1} = '请重新运行crack_length_calibration'; %#ok<AGROW>
        end
        if snr_db(k) < cfg.low_confidence_length_snr_db
            messages{end+1} = sprintf('成像SNR低于%.1f dB，长度结果低可信', ...
                cfg.low_confidence_length_snr_db); %#ok<AGROW>
        end
    elseif measurement.aspect_ratio >= cfg.lof_min_aspect_ratio && ...
            measurement.major_length_mm >= cfg.lof_min_length_mm
        type{k} = 'LOF';
        classification_method{k} = 'CNN non-crack + shape rule';

        % 未熔合标定模型采用横向±15 mm、深度±5 mm的ROI。主流程的通用
        % ±8 mm ROI会裁掉长未熔合的两端，使20 mm缺陷的原始长度饱和在
        % 约16 mm。判为LOF后按标定时相同的ROI重新提取长度，保证训练与
        % 推理阶段的特征定义一致，同时不改变裂纹和气孔的测量范围。
        lof_cfg = cfg;
        % 优先使用未熔合标定模型中保存的ROI，保证训练和主流程特征一致。
        % 旧模型若没有保存这些字段，则回退到主流程默认的横向±15 mm、
        % 深度±5 mm。
        lof_half_width_x = cfg.lof_measure_roi_half_width_x_mm;
        lof_half_width_z = cfg.lof_measure_roi_half_width_z_mm;
        if isfield(lof_calibration,'roi_half_width_x_mm') && ...
                isfinite(lof_calibration.roi_half_width_x_mm)
            lof_half_width_x = lof_calibration.roi_half_width_x_mm;
        end
        if isfield(lof_calibration,'roi_half_width_z_mm') && ...
                isfinite(lof_calibration.roi_half_width_z_mm)
            lof_half_width_z = lof_calibration.roi_half_width_z_mm;
        end
        lof_cfg.measure_roi_half_width_x_mm = lof_half_width_x;
        lof_cfg.measure_roi_half_width_z_mm = lof_half_width_z;
        coarse_lof_length = measurement.major_length_mm;
        measurement = measure_shape(envelope,x_img,z_img, ...
            candidate.center_x_mm,candidate.center_z_mm, ...
            candidate.peak_x_mm,candidate.peak_z_mm,lof_cfg);
        measurements{k} = measurement;
        center_x(k) = measurement.centroid_x_mm;
        center_z(k) = measurement.centroid_z_mm;
        peak_x(k) = measurement.peak_x_mm;
        peak_z(k) = measurement.peak_z_mm;
        raw_length(k) = measurement.major_length_mm;
        angle(k) = measurement.image_angle_deg;
        aspect_ratio(k) = measurement.aspect_ratio;
        snr_db(k) = measurement.snr_db;
        if raw_length(k)-coarse_lof_length >= cfg.lof_roi_expansion_notice_mm
            messages{end+1} = sprintf( ...
                '未熔合扩大ROI后原始长度由%.2f mm更新为%.2f mm', ...
                coarse_lof_length,raw_length(k)); %#ok<AGROW>
        end

        if isfield(lof_calibration,'intercept_mm') && isfield(lof_calibration,'slope')
            corrected_length(k) = lof_calibration.intercept_mm + ...
                lof_calibration.slope*raw_length(k);
            status{k} = 'LOF length calibrated';
            if isfield(lof_calibration,'valid_length_range_mm') && ...
                    (corrected_length(k) < lof_calibration.valid_length_range_mm(1) || ...
                    corrected_length(k) > lof_calibration.valid_length_range_mm(2))
                messages{end+1} = '未熔合长度超出标定范围'; %#ok<AGROW>
            end
        else
            status{k} = 'LOF rule classification; model unavailable';
        end
    else
        type{k} = 'Pore';
        if round_shape_override
            classification_method{k} = 'CNN crack overridden by round-shape + pore-response rule';
            messages{end+1} = sprintf( ...
                'CNN裂纹判定被圆形及孔响应证据覆盖（长宽比%.2f，试算孔径%.3f mm）', ...
                measurement.aspect_ratio,pore_trial.diameter_mm); %#ok<AGROW>
        else
            classification_method{k} = 'CNN non-crack + shape rule';
        end
        if pore_trial_available
            pore_metrics = pore_trial;
        else
            pore_metrics = quantify_pore(envelope,x_img,z_img, ...
                measurement.peak_x_mm,measurement.peak_z_mm,pore_calibration);
        end
        diameter(k) = pore_metrics.diameter_mm;
        area(k) = pore_metrics.area_mm2;
        snr_db(k) = pore_metrics.snr_db;
        if isfield(pore_metrics,'hybrid_branch')
            status{k} = sprintf('Pore calibrated (%s)',pore_metrics.hybrid_branch);
        else
            status{k} = 'Pore diameter/area calibrated';
        end
        if isfield(pore_metrics,'was_bounded') && pore_metrics.was_bounded
            messages{end+1} = '气孔孔径预测触发模型安全边界'; %#ok<AGROW>
        end
        if diameter(k) < pore_calibration.valid_diameter_range_mm(1) || ...
                diameter(k) > pore_calibration.valid_diameter_range_mm(2)
            messages{end+1} = '气孔孔径超出标定范围'; %#ok<AGROW>
        end
        if peak_z(k) < pore_calibration.valid_depth_range_mm(1) || ...
                peak_z(k) > pore_calibration.valid_depth_range_mm(2)
            messages{end+1} = '气孔深度超出标定范围'; %#ok<AGROW>
        end
    end

    % 只在概率真正靠近0.98判定边界时报警；0.999等接近1的结果不再误报。
    if abs(crack_probability(k)-cnn_threshold(k)) < cfg.low_confidence_margin
        messages{end+1} = '裂纹概率接近分类阈值'; %#ok<AGROW>
    end
    if candidate.touches_inspection_boundary
        messages{end+1} = '候选区域接近检测边界'; %#ok<AGROW>
    end
    warning_text{k} = strjoin(messages,'; ');
end

report = table(defect_id,type,classification_method,lof_slag_suggestion, ...
    slag_probability,lof_slag_suggestion_status,crack_probability,cnn_threshold, ...
    center_x,center_z,peak_x,peak_z,global_peak_db,raw_length,corrected_length, ...
    diameter,area,minor_axis_nominal,minor_axis_lower,minor_axis_upper, ...
    area_lower,area_upper,advisory_slag_major,advisory_slag_short_lower, ...
    advisory_slag_short_upper,advisory_slag_area,advisory_slag_area_lower, ...
    advisory_slag_area_upper,angle,aspect_ratio,snr_db,status,warning_text, ...
    'VariableNames',{'DefectID','Type','ClassificationMethod','LOFSlagSuggestion', ...
    'SlagProbability','LOFSlagSuggestionStatus','CrackProbability', ...
    'CNNThreshold','CenterX_mm','CenterZ_mm','PeakX_mm','PeakZ_mm','GlobalPeak_dB', ...
    'RawLength_mm','CorrectedLength_mm','Diameter_mm','Area_mm2', ...
    'MinorAxisNominal_mm','MinorAxisLower_mm','MinorAxisUpper_mm', ...
    'AreaLower_mm2','AreaUpper_mm2','AdvisorySlagMajor_mm', ...
    'AdvisorySlagShortLower_mm','AdvisorySlagShortUpper_mm', ...
    'AdvisorySlagArea_mm2','AdvisorySlagAreaLower_mm2', ...
    'AdvisorySlagAreaUpper_mm2','ImageAngle_deg', ...
    'AspectRatio','SNR_dB','QuantificationStatus','Warning'});

pore_model_version = '';
if isfield(pore_calibration,'version')
    pore_model_version = pore_calibration.version;
end
slag_model_version = '';
if isfield(slag_calibration,'version')
    slag_model_version = slag_calibration.version;
end
lof_slag_advisor_version = '';
if lof_slag_advisor.available && isfield(lof_slag_advisor.model,'version')
    lof_slag_advisor_version = lof_slag_advisor.model.version;
end
image_result = struct('envelope',envelope,'img_db',img_db,'x_img',x_img, ...
    'sample_rate_hz',actual_sample_rate_hz, ...
    'z_img',z_img,'candidates',candidates,'measurements',{measurements}, ...
    'cnn_model',cfg.crack_cnn_model,'cnn_threshold',inference.threshold, ...
    'pore_model',cfg.pore_area_model,'pore_model_version',pore_model_version, ...
    'slag_model',cfg.slag_area_model,'slag_model_version',slag_model_version, ...
    'lof_slag_advisor_model',cfg.lof_slag_classifier_model, ...
    'lof_slag_advisor_version',lof_slag_advisor_version, ...
    'noise_info',noise_info,'output_dir',cfg.output_dir);
save_outputs(report,image_result,input_path,cfg,measurements);

fprintf('\n================ 自动分析结果 ================\n');
disp(report);
fprintf('结果目录: %s\n',cfg.output_dir);
if force_slag_requested
    fprintf('模型：%s。\n',char(string(slag_model_version)));
elseif strlength(string(pore_model_version)) > 0
    fprintf('模型：最新裂纹CNN/局部精细长度、扩大ROI未熔合长度、气孔面积 %s。\n', ...
        char(string(pore_model_version)));
else
    fprintf('模型：最新裂纹CNN/局部精细长度、扩大ROI未熔合长度、气孔面积模型。\n');
end
fprintf('注意：非裂纹的LOF/Pore类别区分仍采用形态规则。\n');
if lof_slag_advisor.available && ~force_slag_requested
    fprintf('LOF/Slag建议器仅提供高置信建议，不会自动改变类型或定量模型。\n');
end
if force_slag_requested
    fprintf('夹渣类型由上游/操作者显式指定；本次未运行裂纹CNN。\n');
end
fprintf('==============================================\n');
end


function cfg = default_config(workspace_dir)
cfg.workspace_dir = workspace_dir;
resource_root = fileparts(fileparts(fileparts(workspace_dir)));
model_root = fullfile(resource_root,'models','paut_v2');
cfg.python_executable = '';
cfg.inference_mode = 'script';
cfg.inference_script = fullfile(resource_root,'algorithms','python','paut_v2', ...
    'predict_crack_batch.py');
cfg.crack_cnn_model = fullfile(model_root,'crack_classifier.pt');
cfg.crack_length_model = fullfile(model_root,'crack_length_model.mat');
% 27组统一背景样本+短裂纹二次深度残差分支的局部精细模型。
cfg.refined_crack_model = fullfile(model_root,'refined_crack_model.mat');
cfg.lof_length_model = fullfile(model_root,'lof_length_model.mat');
% 默认气孔定量采用V32锚点保护混合模型：一般小孔径保留V22，
% 仅对满足门控的0.9 mm疑似响应启用专项分支，>2 mm使用V26。
cfg.pore_area_model = fullfile(model_root,'pore_v32_model.mat');
cfg.slag_area_model = fullfile(model_root,'slag_v84_model.mat');
cfg.lof_slag_classifier_model = fullfile(model_root,'lof_slag_advisor_v1.mat');
cfg.enable_advisory_slag_quantification = true;
% LOF/Slag分类器达到高置信Slag阈值，或上游/操作者明确指定'Slag'
% 时启用夹渣定量；禁止依据案例文件名推断真实类型。
cfg.forced_defect_type = '';

imaging=paut_canonical_imaging_config();
% 输入时间轴决定实际采样率；该值只用于校验前端采集设置。
cfg.expected_sample_rate_hz=[];
cfg.velocity_mps=imaging.velocity_mps;
cfg.tx_z_mm=imaging.tx_z_mm;
cfg.rx_z_mm=imaging.rx_z_mm;
cfg.nested_probe_positions_mm=imaging.probe_positions_mm;
cfg.x_img=imaging.x_img;
cfg.z_img=imaging.z_img;
cfg.bandpass_hz=imaging.bandpass_hz;
cfg.filter_order=imaging.filter_order;
cfg.gate_start_us=imaging.gate_start_us;
cfg.gate_end_us=imaging.gate_end_us;
cfg.gate_transition_us=imaging.gate_transition_us;
cfg.gaussian_sigma_mm=imaging.gaussian_sigma_mm;
% 留空时保持原流程；浅层验证可指定data\2\none，在带通和DAS前逐通道相减。
cfg.background_input_path = '';
cfg.background_scale = 1.0;
cfg.db_floor=imaging.db_floor;
% 仅控制输出图的视觉显示，不参与候选检出、分类或定量计算。
% 默认先按-3 dB截断；若8--35 mm内没有任何保留像素，则本张图放宽到-7 dB。
cfg.display_threshold_db = -3;
cfg.enable_display_threshold_fallback = true;
cfg.display_fallback_threshold_db = -7;
cfg.display_fallback_z_range_mm = [8,35];
cfg.display_background_db = -35;
cfg.display_scale = 8;
% 仅作用于阈值截断输出图：在高分辨率网格上用距离场平滑二值边界，
% 不模糊区域内幅值；面积和主/短轴投影尺寸均不得超过原始掩膜。
cfg.smooth_display_threshold_edges = true;
cfg.display_edge_smoothing_sigma_px = 1.2;
% 留空时按全图峰值归一化；可排除表面强回波，仅影响显示图。
cfg.display_normalization_z_range_mm = [];

cfg.inspection_z_range_mm = [5,37];
cfg.inspection_x_margin_mm = 1.5;
cfg.reject_top_boundary_candidates = true;
cfg.reject_bottom_boundary_candidates = true;
cfg.seed_threshold_db = -8;
cfg.grow_threshold_db = -14;
cfg.fallback_seed_threshold_db = -12;
cfg.fallback_grow_threshold_db = -18;
cfg.minimum_candidate_area_mm2 = 0.20;
cfg.maximum_candidate_area_mm2 = 160;
cfg.patch_size_mm = 16;
cfg.patch_pixels = 256;
cfg.measure_roi_half_size_mm = 8;
% LOF长度模型训练时采用的专用ROI。必须保持一致，否则20 mm左右长缺陷
% 会被通用16 mm宽窗口裁剪并产生长度饱和。
cfg.lof_measure_roi_half_width_x_mm = 15;
cfg.lof_measure_roi_half_width_z_mm = 5;
cfg.lof_roi_expansion_notice_mm = 0.5;
cfg.measure_threshold_db = -10;
cfg.centroid_threshold_db = -12;
% CNN裂纹结果的圆形保护阈值：低于此长宽比时转入非裂纹形态判断。
cfg.crack_min_aspect_ratio = 1.4;
% CNN高度确信且长宽比仅略低于1.4时，不因微小噪声波动强制改判气孔。
% 只有近乎确定的CNN裂纹才允许推翻圆形约束。0.9 mm气孔在
% 0.996--0.998区间会出现伪裂纹，而已验证的极短裂纹均高于0.9999。
cfg.round_shape_guard_probability = 0.9999;
cfg.round_shape_guard_min_aspect_ratio = 1.30;
% 只用于CNN裂纹/圆形气孔冲突时的孔响应证据，允许训练上下界附近的
% 小幅预测漂移；孔径定量本身仍按原标定范围报警，并受3.5 mm硬边界约束。
cfg.pore_response_range_tolerance_mm = 0.15;
% 2~3 mm短裂纹在图像上可能接近圆形。仅在CNN极高置信且粗测尺寸较小时
% 绕过圆形覆盖，避免把端点主导的短裂纹误判为气孔。
cfg.short_crack_guard_probability = 0.9999;
cfg.short_crack_guard_raw_length_range_mm = [1.5,3.3];
cfg.short_crack_guard_angle_range_deg = [75,90];
cfg.lof_min_aspect_ratio = 1.8;
cfg.lof_min_length_mm = 4.0;
cfg.crack_probability_threshold = 0.98;
cfg.low_confidence_margin = 0.01;
% 建模角度约45 deg时，PCA回波长度非单调，暂只给出低可信提示。
cfg.low_confidence_crack_angle_range_deg = [40,55];
cfg.calibration_angle_tolerance_deg = 5;
cfg.enable_refined_crack_model = true;
cfg.refined_crack_angle_range_deg = [45,90];
% 新增独立验证覆盖50至80度；先把精细模型执行范围扩展到45至90度。
% 45至55度仍保留低可信提示，验证合格后再考虑纳入重新训练。
cfg.refined_crack_execution_angle_range_deg = [45,90];
% 高置信短裂纹的成像角可比建模角低约5度，允许进入精细模型。
cfg.short_refined_angle_probability = 0.995;
cfg.short_refined_angle_length_range_mm = [2.0,5.5];
cfg.short_refined_angle_tolerance_range_deg = [50,55];
cfg.refined_length_spread_warning_mm = 1.20;
cfg.low_confidence_length_snr_db = 30;
cfg.refined_crack_step_mm = 0.05;
cfg.refined_crack_roi_half_width_mm = 6;
cfg.save_output_images = true;
% 高斯噪声默认关闭，保持原有主流程结果完全不变。
cfg.enable_gaussian_noise = false;
cfg.gaussian_noise_snr_db = Inf;
cfg.gaussian_noise_seed = 0;
cfg.save_analysis_result = true;
cfg.save_intermediate_files = true;
end


function cfg = apply_config_override(cfg,override)
if ~isstruct(override), error('cfg_override必须是struct。'); end
names = fieldnames(override);
for k = 1:numel(names)
    cfg.(names{k}) = override.(names{k});
end
end


function tf = is_path_under(input_path,root_path)
tf = false;
[input_ok,input_info] = fileattrib(input_path);
[root_ok,root_info] = fileattrib(root_path);
if ~input_ok || ~root_ok, return; end
input_name = lower(strrep(input_info.Name,'/','\'));
root_name = lower(strrep(root_info.Name,'/','\'));
tf = strcmp(input_name,root_name) || startsWith(input_name,[root_name,'\']);
end


function [format,data_folder] = detect_input_format(input_path)
if isfile(input_path)
    raw = load(input_path);
    if size(raw,2) == 257
        format = 'fmc257';
        data_folder = input_path;
        return;
    end
    error('单文件FMC应为时间+256通道，实际为%d列: %s',size(raw,2),input_path);
end
if isfolder(fullfile(input_path,'data'))
    candidate = fullfile(input_path,'data');
else
    candidate = input_path;
end
tx_dirs = dir(fullfile(candidate,'tx_*'));
if ~isempty(tx_dirs)
    format = 'nested';
    data_folder = candidate;
    return;
end
numeric_files = list_numeric_txt(candidate);
if numel(numeric_files) >= 8
    format = 'flat';
    data_folder = candidate;
    return;
end
error('无法识别FMC格式: %s',input_path);
end


function [data_cube,time_axis,tx_positions,rx_positions] = read_fmc(folder,format,cfg)
if strcmp(format,'flat')
    files = list_numeric_txt(folder);
    tx_positions = arrayfun(@(file) str2double(erase(file.name,'.txt')),files);
    first = load(fullfile(files(1).folder,files(1).name));
    n_rx = size(first,2)-2;
    if n_rx ~= numel(files)
        error('平铺FMC的Tx文件数%d与Rx列数%d不一致。',numel(files),n_rx);
    end
    rx_positions = tx_positions;
    time_axis = first(:,2);
    data_cube = zeros(size(first,1),n_rx,numel(files));
    data_cube(:,:,1) = first(:,3:end);
    for tx = 2:numel(files)
        raw = load(fullfile(files(tx).folder,files(tx).name));
        if ~isequal(size(raw),size(first)), error('FMC文件尺寸不一致。'); end
        data_cube(:,:,tx) = raw(:,3:end);
    end
elseif strcmp(format,'nested')
    tx_dirs = dir(fullfile(folder,'tx_*'));
    tx_numbers = arrayfun(@(item) sscanf(item.name,'tx_%d'),tx_dirs);
    [tx_numbers,order] = sort(tx_numbers);
    tx_dirs = tx_dirs(order);
    if any(tx_numbers(:)' ~= 1:numel(tx_dirs))
        error('tx_*目录编号不连续。');
    end
    tx_positions = cfg.nested_probe_positions_mm(tx_numbers);
    first_files = list_numeric_txt(fullfile(tx_dirs(1).folder,tx_dirs(1).name));
    n_rx = numel(first_files);
    rx_positions = cfg.nested_probe_positions_mm(1:n_rx);
    first = load(fullfile(first_files(1).folder,first_files(1).name));
    time_axis = first(:,1);
    data_cube = zeros(size(first,1),n_rx,numel(tx_dirs));
    for tx = 1:numel(tx_dirs)
        rx_files = list_numeric_txt(fullfile(tx_dirs(tx).folder,tx_dirs(tx).name));
        if numel(rx_files) ~= n_rx, error('不同Tx的Rx文件数不一致。'); end
        for rx = 1:n_rx
            raw = load(fullfile(rx_files(rx).folder,rx_files(rx).name));
            data_cube(:,rx,tx) = raw(:,2);
        end
    end
elseif strcmp(format,'fmc257')
    raw = load(folder);
    if size(raw,2) ~= 257
        error('data3 FMC文件应为时间+256通道，实际为%d列。',size(raw,2));
    end
    time_axis = raw(:,1);
    data_cube = reshape(raw(:,2:end),size(raw,1),16,16);
    tx_positions = cfg.nested_probe_positions_mm;
    rx_positions = cfg.nested_probe_positions_mm;
else
    error('未知FMC格式: %s',format);
end
end


function files = list_numeric_txt(folder)
files = dir(fullfile(folder,'*.txt'));
values = arrayfun(@(file) str2double(erase(file.name,'.txt')),files);
valid = isfinite(values);
files = files(valid); values = values(valid);
[~,order] = sort(values);
files = files(order);
end


function validate_background_data(data_cube,time_axis,tx_positions,rx_positions, ...
        background_cube,background_time,background_tx,background_rx)
if ~isequal(size(data_cube),size(background_cube))
    error('公共背景与目标FMC尺寸不同。');
end
if numel(time_axis)~=numel(background_time) || ...
        max(abs(time_axis(:)-background_time(:))) > max(eps(max(abs(time_axis))),1e-12)
    error('公共背景与目标数据的时间轴不一致。');
end
if ~isequal(tx_positions(:),background_tx(:)) || ...
        ~isequal(rx_positions(:),background_rx(:))
    error('公共背景与目标数据的Tx/Rx位置不一致。');
end
end


function [background_cube,background_time] = align_background_data( ...
        data_cube,time_axis,background_cube,background_time) %#ok<INUSD>
% 背景记录可以更长；截取与目标相同的前段，随后再严格核对时间轴。
target_samples=size(data_cube,1); background_samples=size(background_cube,1);
if background_samples < target_samples
    error('公共背景采样点%d少于目标数据%d。',background_samples,target_samples);
end
if background_samples > target_samples
    background_cube=background_cube(1:target_samples,:,:);
    background_time=background_time(1:target_samples);
    fprintf('公共背景由%d点截取到%d点以匹配目标记录。\n', ...
        background_samples,target_samples);
end
end


function [envelope,img_db,x_img,z_img] = reconstruct_das( ...
        data_cube,time_axis,tx_positions,rx_positions,cfg)
[envelope,img_db,x_img,z_img]=paut_reconstruct_das_shared( ...
    data_cube,time_axis,tx_positions,rx_positions,cfg);
end


function candidates = detect_candidates(envelope,x_img,z_img,cfg)
[X,Z] = meshgrid(x_img,z_img);
inspection = Z >= cfg.inspection_z_range_mm(1) & ...
    Z <= cfg.inspection_z_range_mm(2) & ...
    X >= x_img(1)+cfg.inspection_x_margin_mm & ...
    X <= x_img(end)-cfg.inspection_x_margin_mm;
inspection_peak = max(envelope(inspection));
detect_db = 20*log10(envelope/max(inspection_peak,realmin)+eps);
[candidates,found] = candidates_from_thresholds(detect_db,envelope,X,Z,inspection, ...
    cfg.seed_threshold_db,cfg.grow_threshold_db,x_img,z_img,cfg);
if ~found
    [candidates,~] = candidates_from_thresholds(detect_db,envelope,X,Z,inspection, ...
        cfg.fallback_seed_threshold_db,cfg.fallback_grow_threshold_db,x_img,z_img,cfg);
end
if ~isempty(candidates)
    [~,order] = sort([candidates.global_peak_db],'descend');
    candidates = candidates(order);
end
end


function [candidates,found] = candidates_from_thresholds( ...
        detect_db,envelope,X,Z,inspection,seed_threshold,grow_threshold,x_img,z_img,cfg)
seed = detect_db >= seed_threshold & inspection;
grow = detect_db >= grow_threshold & inspection;
pixel_area = median(diff(x_img))*median(diff(z_img));
minimum_pixels = max(1,ceil(cfg.minimum_candidate_area_mm2/pixel_area));
grow = bwareaopen(grow,minimum_pixels,8);
cc = bwconncomp(grow,8);
candidates = struct('center_x_mm',{},'center_z_mm',{}, ...
    'peak_x_mm',{},'peak_z_mm',{},'global_peak_db',{}, ...
    'area_mm2',{},'touches_inspection_boundary',{});
dx = median(diff(x_img));
dz = median(diff(z_img));
x_min = x_img(1)+cfg.inspection_x_margin_mm;
x_max = x_img(end)-cfg.inspection_x_margin_mm;
z_min = cfg.inspection_z_range_mm(1);
z_max = cfg.inspection_z_range_mm(2);
for k = 1:cc.NumObjects
    indices = cc.PixelIdxList{k};
    if ~any(seed(indices)), continue; end
    area_mm2 = numel(indices)*pixel_area;
    if area_mm2 > cfg.maximum_candidate_area_mm2, continue; end
    % 表面强回波的尾部常从检测区上边界进入。只要连通域接触该边界，
    % 就不作为内部缺陷；排除后若无候选，外层会自动启用备用低阈值。
    touches_top_boundary = any(Z(indices) <= z_min+0.5*dz);
    touches_bottom_boundary = any(Z(indices) >= z_max-0.5*dz);
    if cfg.reject_top_boundary_candidates && touches_top_boundary
        continue;
    end
    % 无缺陷基准表明底面反射会在z_max形成多个强伪候选；在候选阶段直接排除。
    if cfg.reject_bottom_boundary_candidates && touches_bottom_boundary
        continue;
    end
    weights = envelope(indices);
    center_x = sum(X(indices).*weights)/sum(weights);
    center_z = sum(Z(indices).*weights)/sum(weights);
    [global_peak_db,peak_offset] = max(detect_db(indices));
    peak_index = indices(peak_offset);
    touches = touches_top_boundary || touches_bottom_boundary || any( ...
        X(indices) <= x_min+0.5*dx | X(indices) >= x_max-0.5*dx);
    item.center_x_mm = center_x;
    item.center_z_mm = center_z;
    item.peak_x_mm = X(peak_index);
    item.peak_z_mm = Z(peak_index);
    item.global_peak_db = global_peak_db;
    item.area_mm2 = area_mm2;
    item.touches_inspection_boundary = touches;
    candidates(end+1) = item; %#ok<AGROW>
end
found = ~isempty(candidates);
end


function patch = extract_patch(img_db,x_img,z_img,center_x,center_z,cfg)
half_size = cfg.patch_size_mm/2;
x_target = linspace(center_x-half_size,center_x+half_size,cfg.patch_pixels);
z_target = linspace(center_z-half_size,center_z+half_size,cfg.patch_pixels);
[Xt,Zt] = meshgrid(x_target,z_target);
[Xs,Zs] = meshgrid(x_img,z_img);
patch = interp2(Xs,Zs,img_db,Xt,Zt,'linear',cfg.db_floor);
patch = single(max(min(patch,0),cfg.db_floor));
end


function inference = run_crack_inference(patch_path,json_path,cfg)
required = {cfg.python_executable,cfg.crack_cnn_model};
if ~strcmpi(cfg.inference_mode,'frozen')
    required{end+1} = cfg.inference_script;
end
for k = 1:numel(required)
    if ~isfile(required{k}), error('推理依赖不存在: %s',required{k}); end
end
if strcmpi(cfg.inference_mode,'frozen')
    command = sprintf('"%s" --paut-inference --patches "%s" --model "%s" --output "%s"', ...
        cfg.python_executable,patch_path,cfg.crack_cnn_model,json_path);
else
    command = sprintf('"%s" "%s" --patches "%s" --model "%s" --output "%s"', ...
        cfg.python_executable,cfg.inference_script,patch_path,cfg.crack_cnn_model,json_path);
end
[status,output] = system(command);
if status ~= 0
    error('裂纹CNN推理失败:\n%s',output);
end
inference = jsondecode(fileread(json_path));
inference.probabilities = double(inference.probabilities(:));
inference.predictions = double(inference.predictions(:));
end


function calibration = load_calibration(path,label)
if ~isfile(path)
    warning('%s模型不存在: %s',label,path);
    calibration = struct();
    return;
end
loaded = load(path,'model');
calibration = loaded.model;
end


function calibration = load_pore_calibration(path)
if ~isfile(path)
    error('气孔面积模型不存在: %s',path);
end
loaded = load(path,'model');
if ~isfield(loaded,'model')
    error('气孔面积模型文件中缺少model变量: %s',path);
end
container = loaded.model;
if isfield(container,'augmented')
    % 新孔径独立验证通过的25组V2.2增强模型。训练容器同时保存了
    % baseline和augmented，主流程只使用未包含0.8/1.0/1.8验证组的后者。
    calibration = container.augmented;
    calibration.model_family = 'v22_monotonic';
    calibration.version = container.version;
    calibration.reference_amplitude = container.reference_amplitude;
    calibration.processing_config = container.processing_config;
    calibration.valid_diameter_range_mm = [ ...
        min(calibration.training_diameters_mm), ...
        max(calibration.training_diameters_mm)];
    calibration.valid_depth_range_mm = [10,30];
    if isfield(container.processing_config,'new_v22_diameter_bounds_mm')
        calibration.hard_diameter_bounds_mm = ...
            container.processing_config.new_v22_diameter_bounds_mm;
    else
        calibration.hard_diameter_bounds_mm = [0.20,3.50];
    end
elseif isfield(container,'hybrid_small') && isfield(container,'hybrid_large')
    calibration = container;
    calibration.model_family = 'hybrid_v22_v26';
elseif isfield(container,'calibrator') && ...
        isfield(container.calibrator,'peak_depth_quadratic')
    calibration = container.calibrator;
    calibration.model_family = 'v22_monotonic';
    calibration.version = container.version;
    calibration.reference_amplitude = container.reference_amplitude;
    calibration.processing_config = container.processing_config;
    calibration.valid_diameter_range_mm = container.valid_diameter_range_mm;
    calibration.valid_depth_range_mm = container.valid_depth_range_mm;
    calibration.hard_diameter_bounds_mm = container.hard_diameter_bounds_mm;
else
    % 兼容旧版岭回归气孔模型，便于通过cfg_override临时复现实验结果。
    calibration = container;
    calibration.model_family = 'legacy_ridge';
end
end


function calibration = load_slag_calibration(path)
if ~isfile(path)
    error('夹渣定量模型不存在: %s',path);
end
loaded=load(path,'model');
if ~isfield(loaded,'model') || ~isfield(loaded.model,'major_model') || ...
        ~isfield(loaded.model,'short_axis_range_mm') || ...
        ~isfield(loaded.model,'measure_roi_half_width_x_mm') || ...
        ~isfield(loaded.model,'measure_roi_half_width_z_mm')
    error('夹渣定量模型格式不兼容: %s',path);
end
calibration=loaded.model;
end


function advisor=load_lof_slag_advisor(path)
advisor=struct('available',false,'model',struct());
if ~isfile(path)
    warning('LOF/Slag分类建议器不存在，将跳过建议: %s',path);
    return;
end
loaded=load(path,'model');
required={'large_strip_classifier','feature_names', ...
    'large_strip_lof_probability_max','large_strip_slag_probability_min', ...
    'valid_depth_range_mm'};
if ~isfield(loaded,'model') || ~all(isfield(loaded.model,required))
    warning('LOF/Slag分类建议器格式不兼容，将跳过建议: %s',path);
    return;
end
advisor.available=true;
advisor.model=loaded.model;
end


function model = load_refined_crack_model(path)
model = struct('available',false);
if ~isfile(path)
    warning('60至90度局部精细裂纹模型不存在: %s',path);
    return;
end
loaded = load(path,'crack_model');
if ~isfield(loaded,'crack_model') || ~isfield(loaded.crack_model,'available') || ...
        ~loaded.crack_model.available
    warning('局部精细裂纹模型文件中没有可用的crack_model。');
    return;
end
model = loaded.crack_model;
end


function refined = quantify_refined_crack(data_cube,time_axis,tx_positions, ...
        rx_positions,coarse_x,coarse_z,coarse_raw_length,coarse_gate_length,cfg,model)
refined = struct('available',false);
half_width = cfg.refined_crack_roi_half_width_mm;
if isfinite(coarse_raw_length)
    half_width = max(half_width,min(14,coarse_raw_length+2));
end
local_cfg = cfg;
local_cfg.x_img = max(20,coarse_x-half_width):cfg.refined_crack_step_mm: ...
    min(80,coarse_x+half_width);
local_cfg.z_img = max(0,coarse_z-half_width):cfg.refined_crack_step_mm: ...
    min(40,coarse_z+half_width);
local_cfg.inspection_z_range_mm = [local_cfg.z_img(1),local_cfg.z_img(end)];
local_cfg.inspection_x_margin_mm = 0.25;
local_cfg.reject_top_boundary_candidates = false;
local_cfg.reject_bottom_boundary_candidates = false;
local_cfg.minimum_candidate_area_mm2 = 0.05;

[local_envelope,~,local_x,local_z] = reconstruct_das(data_cube,time_axis, ...
    tx_positions,rx_positions,local_cfg);
local_candidates = detect_candidates(local_envelope,local_x,local_z,local_cfg);
if isempty(local_candidates), return; end
distances = arrayfun(@(c) hypot(c.center_x_mm-coarse_x,c.center_z_mm-coarse_z), ...
    local_candidates);
[~,selected] = min(distances);
candidate = local_candidates(selected);

thresholds = [-6,-10,-12];
shared_features = extract_crack_multithreshold_features(local_envelope, ...
    local_x,local_z,candidate.peak_x_mm,candidate.peak_z_mm,thresholds);
lengths = [shared_features.length_mm];
% SNR等显示量仍由原测量函数提供；长度、角度、端点中点则必须
% 与训练阶段共用同一个多阈值PCA定义。
measure_cfg = local_cfg; measure_cfg.measure_threshold_db = -10;
m10 = measure_shape(local_envelope,local_x,local_z, ...
    candidate.center_x_mm,candidate.center_z_mm, ...
    candidate.peak_x_mm,candidate.peak_z_mm,measure_cfg);
m10.pca_midpoint_x_mm = shared_features(2).midpoint_x_mm;
m10.pca_midpoint_z_mm = shared_features(2).midpoint_z_mm;
m10.image_angle_deg = shared_features(2).angle_deg;
m10.aspect_ratio = shared_features(2).aspect_ratio;
estimated_angle = 90-abs(m10.image_angle_deg);
features = crack_length_feature_vector(model.feature_variant,lengths, ...
    estimated_angle,m10.pca_midpoint_z_mm);
if any(~isfinite(features)) || numel(model.feature_mean) ~= numel(features)
    return;
end
scaled = (features-model.feature_mean)./model.feature_std;
prediction = [1,scaled]*model.coefficients;
if isfield(model,'log_target') && model.log_target, prediction = exp(prediction); end
if ~isfinite(prediction) || prediction <= 0, return; end

base_prediction = prediction;
short_residual = 0;
short_residual_applied = false;
if isfield(model,'short_residual_model') && model.short_residual_model.available
    residual_model = model.short_residual_model;
    length_allowed = true;
    if isfield(residual_model,'execution_length_range_mm')
        length_allowed = prediction >= residual_model.execution_length_range_mm(1) && ...
            prediction <= residual_model.execution_length_range_mm(2);
    end
    if length_allowed && estimated_angle >= residual_model.execution_angle_range_deg(1) && ...
            estimated_angle <= residual_model.execution_angle_range_deg(2) && ...
            m10.pca_midpoint_z_mm >= residual_model.execution_depth_range_mm(1) && ...
            m10.pca_midpoint_z_mm <= residual_model.execution_depth_range_mm(2)
        short_X = crack_length_feature_vector(residual_model.feature_variant, ...
            lengths,estimated_angle,m10.pca_midpoint_z_mm,prediction);
        short_Z = (short_X-residual_model.feature_mean)./residual_model.feature_std;
        short_residual = [1,short_Z]*residual_model.coefficients;
        if isfinite(short_residual)
            short_residual_applied = true;
        else
            short_residual = 0;
        end
    end
end

long_residual = 0;
long_residual_applied = false;
if isfield(model,'long_residual_model') && model.long_residual_model.available
    residual_model = model.long_residual_model;
    if estimated_angle >= residual_model.execution_angle_range_deg(1) && ...
            estimated_angle <= residual_model.execution_angle_range_deg(2) && ...
            m10.pca_midpoint_z_mm >= residual_model.execution_depth_range_mm(1) && ...
            m10.pca_midpoint_z_mm <= residual_model.execution_depth_range_mm(2)
        long_X = crack_length_feature_vector(residual_model.feature_variant, ...
            lengths,estimated_angle,m10.pca_midpoint_z_mm,base_prediction);
        long_Z = (long_X-residual_model.feature_mean)./residual_model.feature_std;
        long_residual = [1,long_Z]*residual_model.coefficients;
        if isfinite(long_residual)
            long_residual_applied = true;
        else
            long_residual = 0;
        end
    end
end

dual_branch_applied = isfield(model,'branch_blend_range_mm') && ...
    isfield(model,'long_residual_model');
long_branch_weight = 0;
if dual_branch_applied
    blend_range=model.branch_blend_range_mm;
    long_branch_weight=max(0,min(1,(coarse_gate_length-blend_range(1))/ ...
        (blend_range(2)-blend_range(1))));
    prediction=base_prediction+(1-long_branch_weight)*short_residual+ ...
        long_branch_weight*long_residual;
elseif short_residual_applied
    prediction=base_prediction+short_residual;
end

refined.available = true;
refined.length6_mm = lengths(1);
refined.length10_mm = lengths(2);
refined.length12_mm = lengths(3);
refined.length_spread_mm = max(lengths)-min(lengths);
refined.corrected_length_mm = prediction;
refined.center_x_mm = m10.pca_midpoint_x_mm;
refined.center_z_mm = m10.pca_midpoint_z_mm;
refined.image_angle_deg = m10.image_angle_deg;
refined.estimated_model_angle_deg = estimated_angle;
refined.aspect_ratio = m10.aspect_ratio;
refined.snr_db = m10.snr_db;
refined.short_residual_applied = short_residual_applied;
refined.long_residual_applied = long_residual_applied;
refined.dual_branch_applied = dual_branch_applied;
refined.long_branch_weight = long_branch_weight;
end


function features = refined_model_features(model,lengths,estimated_angle,depth_mm)
% 按训练时记录的特征版本构造输入，避免新旧模型特征含义错位。
l6 = lengths(1); l10 = lengths(2); l12 = lengths(3);
delta10 = l10-l6; delta12 = l12-l6;
variant = "";
if isfield(model,'feature_variant'), variant = string(model.feature_variant); end
switch variant
    case "L6线性"
        features = l6;
    case "L6+扩张+角度+深度"
        features = [l6,delta10,estimated_angle,depth_mm];
    case "L6二次+扩张+角度+深度"
        features = [l6,l6.^2,delta10,estimated_angle,depth_mm];
    case "L6+双阈值扩张+角度+深度"
        features = [l6,delta10,delta12,estimated_angle,depth_mm];
    otherwise
        % 兼容旧版五特征模型。
        features = [l6,l10,l12,estimated_angle,depth_mm];
end
end


function measurement = measure_shape(envelope,x_img,z_img,center_x,center_z, ...
        candidate_peak_x,candidate_peak_z,cfg)
roi_half_width_x = cfg.measure_roi_half_size_mm;
roi_half_width_z = cfg.measure_roi_half_size_mm;
if isfield(cfg,'measure_roi_half_width_x_mm')
    roi_half_width_x = cfg.measure_roi_half_width_x_mm;
end
if isfield(cfg,'measure_roi_half_width_z_mm')
    roi_half_width_z = cfg.measure_roi_half_width_z_mm;
end
x_mask = abs(x_img-center_x) <= roi_half_width_x;
z_mask = abs(z_img-center_z) <= roi_half_width_z & ...
    z_img >= cfg.inspection_z_range_mm(1) & ...
    z_img <= cfg.inspection_z_range_mm(2);
x_roi = x_img(x_mask); z_roi = z_img(z_mask);
roi = envelope(z_mask,x_mask);
% 测量种子固定为候选连通域自己的峰值，不能再跳到ROI中的表面强峰。
[~,seed_col] = min(abs(x_roi-candidate_peak_x));
[~,seed_row] = min(abs(z_roi-candidate_peak_z));
seed = sub2ind(size(roi),seed_row,seed_col);
peak = roi(seed);
local_db = 20*log10(roi/max(peak,realmin)+eps);
mask = component_at_seed(local_db >= cfg.measure_threshold_db,seed);
[row,col] = find(mask);
x_points = x_roi(col(:));
z_points = z_roi(row(:));
points = [x_points(:),z_points(:)];
point_center = mean(points,1);
centered = bsxfun(@minus,points,point_center);
covariance = (centered'*centered)/max(size(centered,1)-1,1);
[vectors,values] = eig(covariance);
[~,major_index] = max(diag(values));
major_axis = vectors(:,major_index);
if major_axis(1) < 0, major_axis = -major_axis; end
minor_axis = [-major_axis(2);major_axis(1)];
dx = median(diff(x_img)); dz = median(diff(z_img));
major_projection = centered*major_axis;
minor_projection = centered*minor_axis;
major_pixel = abs(major_axis(1))*dx+abs(major_axis(2))*dz;
minor_pixel = abs(minor_axis(1))*dx+abs(minor_axis(2))*dz;
projection_min = min(major_projection);
projection_max = max(major_projection);
major_length = projection_max-projection_min+major_pixel;
minor_length = max(minor_projection)-min(minor_projection)+minor_pixel;

% 将连通域沿PCA主轴投影，主轴两端点及其中点用于裂纹几何定位。
pca_endpoint_1 = point_center + projection_min*major_axis.';
pca_endpoint_2 = point_center + projection_max*major_axis.';
pca_midpoint = (pca_endpoint_1+pca_endpoint_2)/2;

centroid_mask = component_at_seed(local_db >= cfg.centroid_threshold_db,seed);
[Xroi,Zroi] = meshgrid(x_roi,z_roi);
weights = roi.*centroid_mask;
centroid_x = sum(Xroi(:).*weights(:))/max(sum(weights(:)),realmin);
centroid_z = sum(Zroi(:).*weights(:))/max(sum(weights(:)),realmin);
distance = hypot(Xroi-x_roi(seed_col),Zroi-z_roi(seed_row));
noise = roi(distance >= 5 & distance <= 7);
if isempty(noise), noise = roi(:); end
snr_db = 20*log10(max(peak,realmin)/max(median(noise),realmin));

measurement.peak_x_mm = x_roi(seed_col);
measurement.peak_z_mm = z_roi(seed_row);
measurement.centroid_x_mm = centroid_x;
measurement.centroid_z_mm = centroid_z;
measurement.pca_endpoint_1_x_mm = pca_endpoint_1(1);
measurement.pca_endpoint_1_z_mm = pca_endpoint_1(2);
measurement.pca_endpoint_2_x_mm = pca_endpoint_2(1);
measurement.pca_endpoint_2_z_mm = pca_endpoint_2(2);
measurement.pca_midpoint_x_mm = pca_midpoint(1);
measurement.pca_midpoint_z_mm = pca_midpoint(2);
measurement.major_length_mm = major_length;
measurement.minor_length_mm = minor_length;
measurement.aspect_ratio = major_length/max(minor_length,eps);
measurement.image_angle_deg = atan2d(major_axis(2),major_axis(1));
measurement.area_mm2 = nnz(mask)*dx*dz;
measurement.snr_db = snr_db;
measurement.mask = mask;
measurement.x_roi = x_roi;
measurement.z_roi = z_roi;
end


function feature=extract_lof_slag_advisor_features(envelope,x_img,z_img,peak_x,peak_z)
% 与train_lof_slag_classifier_v1完全相同的公共ROI和多阈值特征。
x_mask=abs(x_img-peak_x)<=8;
z_mask=abs(z_img-peak_z)<=5 & z_img>=1;
roi=envelope(z_mask,x_mask); xv=x_img(x_mask); zv=z_img(z_mask);
[X,Z]=meshgrid(xv,zv); [peak,seed]=max(roi(:));
db=20*log10(roi/max(peak,realmin)+eps); thresholds=[-6,-9,-12,-15];
major=nan(1,4); minor=nan(1,4); component_area=nan(1,4);
fill=nan(1,4); image_angle=nan(1,4);
dx=median(diff(x_img)); dz=median(diff(z_img));
for j=1:numel(thresholds)
    mask=component_at_seed(db>=thresholds(j),seed);
    [major(j),minor(j),image_angle(j)]=advisor_pca_extents(mask,X,Z,dx,dz);
    component_area(j)=nnz(mask)*dx*dz;
    fill(j)=component_area(j)/max(major(j)*minor(j),dx*dz);
end
pixel=max(dx,dz);
feature=table(log(max(minor(1),pixel)),log(max(minor(3),pixel)), ...
    log(max(minor(4),pixel)), ...
    log(max(minor(4),pixel))-log(max(minor(1),pixel)), ...
    log(max(major(3)/max(minor(3),pixel),1)), ...
    log(max(component_area(3),dx*dz)),fill(3),abs(image_angle(3)), ...
    'VariableNames',{'LogMinor6','LogMinor12','LogMinor15','MinorGrowth6to15', ...
    'LogAspect12','LogArea12','Fill12','AbsAngle12'});
end


function [major,minor,image_angle]=advisor_pca_extents(mask,X,Z,dx,dz)
indices=find(mask); pixel=max(dx,dz);
if numel(indices)<3
    major=pixel; minor=pixel; image_angle=0; return;
end
points=[X(indices),Z(indices)]; centered=points-mean(points,1);
covariance=(centered'*centered)/size(centered,1);
[vectors,values]=eig(covariance); [~,order]=sort(diag(values),'descend');
vectors=vectors(:,order);
if vectors(1,1)<0, vectors(:,1)=-vectors(:,1); end
u=centered*vectors(:,1); v=centered*vectors(:,2);
major=max(u)-min(u)+dx*abs(vectors(1,1))+dz*abs(vectors(2,1));
minor=max(v)-min(v)+dx*abs(vectors(1,2))+dz*abs(vectors(2,2));
image_angle=atan2d(vectors(2,1),vectors(1,1));
end


function metrics = quantify_pore(envelope,x_img,z_img,center_x,center_z,model)
if isempty(fieldnames(model))
    error('气孔面积模型不可用。');
end
if isfield(model,'model_family') && strcmp(model.model_family,'hybrid_v22_v26')
    metrics = quantify_pore_hybrid(envelope,x_img,z_img,center_x,center_z,model);
elseif isfield(model,'model_family') && strcmp(model.model_family,'v22_monotonic')
    metrics = quantify_pore_v22(envelope,x_img,z_img,center_x,center_z,model);
else
    metrics = quantify_pore_legacy(envelope,x_img,z_img,center_x,center_z,model);
end
end

function metrics = quantify_pore_hybrid(envelope,x_img,z_img,center_x,center_z,model)
if isfield(model,'hybrid_router')
    route_metrics = quantify_pore_v22(envelope,x_img,z_img,center_x,center_z, ...
        model.hybrid_router);
else
    route_metrics = quantify_pore_v22(envelope,x_img,z_img,center_x,center_z, ...
        model.hybrid_small);
end
if route_metrics.diameter_mm <= model.switch_diameter_mm
    use_target09 = false;
    if isfield(model,'hybrid_target09') && ...
            isfield(model,'target09_routing_range_mm')
        in_target_range = route_metrics.diameter_mm >= ...
            model.target09_routing_range_mm(1) && ...
            route_metrics.diameter_mm <= model.target09_routing_range_mm(2);
        protected_anchor = false;
        if isfield(model,'target09_anchor_protection')
            anchor = model.target09_anchor_protection;
            protected_anchor = route_metrics.peak_x_mm <= anchor.maximum_x_mm && ...
                route_metrics.peak_z_mm >= anchor.depth_range_mm(1) && ...
                route_metrics.peak_z_mm <= anchor.depth_range_mm(2);
        end
        use_target09 = in_target_range && ~protected_anchor;
    end
    if use_target09
        metrics = quantify_pore_v22(envelope,x_img,z_img,center_x,center_z, ...
            model.hybrid_target09);
        if isfield(model,'target09_branch_label')
            metrics.hybrid_branch = model.target09_branch_label;
        else
            metrics.hybrid_branch = 'target09-specialist';
        end
    elseif isfield(model,'small_equals_router') && model.small_equals_router
        metrics = route_metrics;
    elseif isfield(model,'hybrid_router')
        metrics = quantify_pore_v22(envelope,x_img,z_img,center_x,center_z, ...
            model.hybrid_small);
    else
        metrics = route_metrics;
    end
    if ~use_target09
        if isfield(model,'small_branch_label')
            metrics.hybrid_branch = model.small_branch_label;
        else
            metrics.hybrid_branch = 'v22-small';
        end
    end
else
    metrics = quantify_pore_v22(envelope,x_img,z_img,center_x,center_z, ...
        model.hybrid_large);
    if isfield(model,'large_branch_label')
        metrics.hybrid_branch = model.large_branch_label;
    else
        metrics.hybrid_branch = 'v26-large';
    end
end
metrics.hybrid_routing_diameter_mm = route_metrics.diameter_mm;
end


function metrics = quantify_pore_v22(envelope,x_img,z_img,center_x,center_z,model)
cfg = model.processing_config;
x_mask = abs(x_img-center_x) <= cfg.measure_roi_half_width_mm;
z_mask = abs(z_img-center_z) <= cfg.measure_roi_half_width_mm;
if ~any(x_mask) || ~any(z_mask)
    error('气孔局部ROI超出成像区域。');
end
x_roi=x_img(x_mask); z_roi=z_img(z_mask);
roi=envelope(z_mask,x_mask);
[Xroi,Zroi]=meshgrid(x_roi,z_roi);
[peak,seed]=max(roi(:));
[seed_row,seed_col]=ind2sub(size(roi),seed);
peak_x=x_roi(seed_col); peak_z=z_roi(seed_row);
distance=hypot(Xroi-peak_x,Zroi-peak_z);
pixel_area=median(diff(x_img))*median(diff(z_img));
energy_mask=distance<=cfg.energy_radius_mm;
relative_roi=roi/model.reference_amplitude;
peak_relative=peak/model.reference_amplitude;
energy_relative=sum(relative_roi(energy_mask).^2)*pixel_area;

noise_mask=distance>=cfg.noise_inner_radius_mm & ...
    distance<=cfg.noise_outer_radius_mm & Zroi>=cfg.minimum_noise_depth_mm;
noise=roi(noise_mask);
if isempty(noise), noise=roi(:); end
snr_db=20*log10(max(peak,realmin)/max(median(noise),realmin));

x_side=double(peak_x>=mean(cfg.probe_positions_mm));
depth=(peak_z-cfg.depth_reference_mm)/cfg.new_v22_depth_scale_mm;
peak_depth_side=0; peak_depth2_side=0;
energy_depth_side=0; energy_depth2_side=0;
if isfield(model,'peak_depth_xside_slope')
    peak_depth_side=model.peak_depth_xside_slope;
end
if isfield(model,'peak_depth2_xside_slope')
    peak_depth2_side=model.peak_depth2_xside_slope;
end
if isfield(model,'energy_depth_xside_slope')
    energy_depth_side=model.energy_depth_xside_slope;
end
if isfield(model,'energy_depth2_xside_slope')
    energy_depth2_side=model.energy_depth2_xside_slope;
end
corrected_peak=log(max(peak_relative,realmin))- ...
    model.peak_depth_linear*depth-model.peak_depth_quadratic*depth.^2- ...
    model.peak_xside_slope*x_side-peak_depth_side*depth*x_side- ...
    peak_depth2_side*depth.^2*x_side;
corrected_energy=log(max(energy_relative,realmin))- ...
    model.energy_depth_linear*depth-model.energy_depth_quadratic*depth.^2- ...
    model.energy_xside_slope*x_side-energy_depth_side*depth*x_side- ...
    energy_depth2_side*depth.^2*x_side;
standardized=([corrected_peak,corrected_energy]-model.feature_mean)./ ...
    model.feature_std;
response=standardized*model.response_weights;
[diameter_mm,was_bounded]=bounded_monotonic_diameter(response, ...
    model.response_knots,model.log_diameter_knots,model.hard_diameter_bounds_mm);

shape_feature = NaN;
shape_correction_log = 0;
if isfield(model,'shape_residual') && model.shape_residual.enabled
    local_db = 20*log10(roi/max(peak,realmin)+eps);
    mask6 = component_at_seed(local_db >= -6,seed);
    mask9 = component_at_seed(local_db >= -9,seed);
    mask12 = component_at_seed(local_db >= -12,seed);
    area6 = nnz(mask6)*pixel_area;
    area9 = nnz(mask9)*pixel_area;
    area12 = nnz(mask12)*pixel_area;
    residual = model.shape_residual;
    switch residual.feature_name
        case 'LogArea12Area6Ratio'
            shape_feature = log(max(area12,1e-8)/max(area6,1e-8));
        case 'LogArea9Area6Ratio'
            shape_feature = log(max(area9,1e-8)/max(area6,1e-8));
        case 'LogArea12Area9Ratio'
            shape_feature = log(max(area12,1e-8)/max(area9,1e-8));
        case 'LogArea12'
            shape_feature = log(max(area12,1e-8));
        otherwise
            error('不支持的单一形态残差特征: %s',residual.feature_name);
    end
    shape_correction_log = residual.intercept+residual.slope* ...
        (shape_feature-residual.shape_mean)/residual.shape_std;
    if isfield(residual,'use_position_residual') && residual.use_position_residual
        position_design = [depth,depth.^2,x_side,depth*x_side];
        shape_correction_log = shape_correction_log+ ...
            position_design*residual.position_coefficients;
    end
    if isfield(residual,'use_target09_residual') && residual.use_target09_residual
        target_width = max(residual.gate_width_mm,1e-6);
        shape_correction_log = shape_correction_log* ...
            exp(-0.5*((diameter_mm-0.9)/target_width)^2);
    elseif isfield(residual,'use_small_pore_gate') && residual.use_small_pore_gate
        gate_argument = (diameter_mm-residual.gate_center_mm)/ ...
            max(residual.gate_width_mm,1e-6);
        gate_argument = max(min(gate_argument,40),-40);
        small_pore_gate = 1/(1+exp(gate_argument));
        shape_correction_log = shape_correction_log*small_pore_gate;
    end
    shape_correction_log = max(min(shape_correction_log,residual.max_abs_log), ...
        -residual.max_abs_log);
    corrected_unbounded = diameter_mm*exp(shape_correction_log);
    corrected_diameter = max(min(corrected_unbounded,model.hard_diameter_bounds_mm(2)), ...
        model.hard_diameter_bounds_mm(1));
    was_bounded = was_bounded || abs(corrected_diameter-corrected_unbounded)>1e-12;
    diameter_mm = corrected_diameter;
end

metrics.diameter_mm=diameter_mm;
metrics.area_mm2=pi*diameter_mm^2/4;
metrics.snr_db=snr_db;
metrics.peak_x_mm=peak_x;
metrics.peak_z_mm=peak_z;
metrics.peak_relative=peak_relative;
metrics.energy_relative_mm2=energy_relative;
metrics.composite_response=response;
metrics.shape_feature=shape_feature;
metrics.shape_correction_log=shape_correction_log;
metrics.was_bounded=was_bounded;
end


function [diameter_mm,was_bounded] = bounded_monotonic_diameter( ...
        response,response_knots,log_diameter_knots,diameter_bounds)
knots=response_knots(:);
log_d=log_diameter_knots(:);
if numel(knots)<2 || numel(knots)~=numel(log_d)
    error('气孔V2.2单调曲线节点无效。');
end
if response>=knots(1) && response<=knots(end)
    prediction_log=interp1(knots,log_d,response,'pchip');
elseif response<knots(1)
    slope=min(max((log_d(2)-log_d(1))/max(knots(2)-knots(1),1e-8),0),5);
    prediction_log=log_d(1)+slope*(response-knots(1));
else
    slope=min(max((log_d(end)-log_d(end-1))/ ...
        max(knots(end)-knots(end-1),1e-8),0),5);
    prediction_log=log_d(end)+slope*(response-knots(end));
end
unbounded=exp(prediction_log);
diameter_mm=max(min(unbounded,diameter_bounds(2)),diameter_bounds(1));
was_bounded=abs(diameter_mm-unbounded)>1e-12;
end


function metrics = quantify_pore_legacy(envelope,x_img,z_img,center_x,center_z,model)
cfg = model.processing_config;
gain = exp(-model.depth_slope_log_amplitude_per_mm* ...
    (z_img(:)-model.depth_reference_mm));
max_gain = 10^(cfg.max_depth_gain_db/20);
gain = min(max(gain,1/max_gain),max_gain);
compensated = bsxfun(@times,envelope,gain);

x_mask = abs(x_img-center_x) <= cfg.measure_roi_half_width_mm;
z_mask = abs(z_img-center_z) <= cfg.measure_roi_half_width_mm;
x_roi = x_img(x_mask); z_roi = z_img(z_mask);
roi = compensated(z_mask,x_mask);
[Xroi,Zroi] = meshgrid(x_roi,z_roi);
[peak,seed] = max(roi(:));
[seed_row,seed_col] = ind2sub(size(roi),seed);
peak_x = x_roi(seed_col); peak_z = z_roi(seed_row);
local_db = 20*log10(roi/max(peak,realmin)+eps);
mask12 = component_at_seed(local_db >= -12,seed);
pixel_area = median(diff(x_img))*median(diff(z_img));
distance = hypot(Xroi-peak_x,Zroi-peak_z);
energy_mask = distance <= cfg.energy_radius_mm;
relative_roi = roi/model.reference_amplitude;
energy_relative = sum(relative_roi(energy_mask).^2)*pixel_area;
noise_mask = distance >= cfg.noise_inner_radius_mm & ...
    distance <= cfg.noise_outer_radius_mm & Zroi >= cfg.minimum_noise_depth_mm;
noise = roi(noise_mask);
if isempty(noise), noise = roi(:); end
snr_db = 20*log10(max(peak,realmin)/max(median(noise),realmin));
features = [log10(max(peak/model.reference_amplitude,realmin)), ...
    log10(max(energy_relative,realmin)),nnz(mask12)*pixel_area,peak_z];
standardized = (features-model.feature_mean)./model.feature_std;
log_diameter = model.intercept+standardized*model.coefficients;
diameter_mm = exp(log_diameter);
metrics.diameter_mm = diameter_mm;
metrics.area_mm2 = pi*diameter_mm^2/4;
metrics.snr_db = snr_db;
metrics.was_bounded = false;
end


function component = component_at_seed(mask,seed)
component = false(size(mask));
cc = bwconncomp(mask,8);
for k = 1:cc.NumObjects
    if any(cc.PixelIdxList{k} == seed)
        component(cc.PixelIdxList{k}) = true;
        return;
    end
end
end


function save_outputs(report,image_result,input_path,cfg,measurements)
writetable(report,fullfile(cfg.output_dir,'defect_report.csv'));
if isfield(cfg,'save_intermediate_files') && ~cfg.save_intermediate_files
    delete_if_exists(fullfile(cfg.output_dir,'candidate_patches.mat'));
    delete_if_exists(fullfile(cfg.output_dir,'crack_inference.json'));
end
if isfield(cfg,'save_output_images') && ~cfg.save_output_images
    if ~isfield(cfg,'save_analysis_result') || cfg.save_analysis_result
        save(fullfile(cfg.output_dir,'analysis_result.mat'), ...
            'report','image_result','input_path','cfg','-v7.3');
    end
    return;
end

% 全部采用简洁样式：无标题、无颜色条、无坐标轴文字，仅保留图像与坐标轴刻度。
x_display = linspace(image_result.x_img(1),image_result.x_img(end), ...
    (numel(image_result.x_img)-1)*cfg.display_scale+1);
z_display = linspace(image_result.z_img(1),image_result.z_img(end), ...
    (numel(image_result.z_img)-1)*cfg.display_scale+1);
[X_display,Z_display] = meshgrid(x_display,z_display);
img_db_visual_full = interp2(image_result.x_img,image_result.z_img, ...
    image_result.img_db,X_display,Z_display,'linear');

% 使用与最新气孔定量一致的二次深度响应，计算第二张阈值截断图。
pore_model = load_pore_calibration(cfg.pore_area_model);
img_db_compensated = depth_compensated_display_db( ...
    image_result.envelope,image_result.z_img,pore_model,cfg.db_floor, ...
    cfg.display_normalization_z_range_mm);

primary_display_threshold_db = cfg.display_threshold_db;
display_threshold_db = primary_display_threshold_db;
fallback_applied = false;
if isfield(cfg,'enable_display_threshold_fallback') && ...
        cfg.enable_display_threshold_fallback
    fallback_range = cfg.display_fallback_z_range_mm;
    if numel(fallback_range) ~= 2 || fallback_range(1) > fallback_range(2)
        error('display_fallback_z_range_mm必须是递增的两个深度值。');
    end
    fallback_rows = image_result.z_img >= fallback_range(1) & ...
        image_result.z_img <= fallback_range(2);
    primary_mask_in_range = img_db_compensated(fallback_rows,:) >= ...
        display_threshold_db;
    if ~any(primary_mask_in_range(:))
        if cfg.display_fallback_threshold_db >= display_threshold_db
            error('备用显示阈值必须低于主显示阈值。');
        end
        display_threshold_db = cfg.display_fallback_threshold_db;
        fallback_applied = true;
    end
end
image_result.display_threshold_db_used = display_threshold_db;
image_result.display_threshold_fallback_applied = fallback_applied;
image_result.display_threshold_check_z_range_mm = ...
    cfg.display_fallback_z_range_mm;
if fallback_applied
    fprintf(['阈值截断显示：%.1f--%.1f mm内按 %.1f dB无保留像素，' ...
        '本张图已自动放宽到 %.1f dB。\n'],fallback_range(1),fallback_range(2), ...
        primary_display_threshold_db,display_threshold_db);
else
    fprintf('阈值截断显示：使用 %.1f dB。\n',display_threshold_db);
end

if ~isfield(cfg,'save_analysis_result') || cfg.save_analysis_result
    save(fullfile(cfg.output_dir,'analysis_result.mat'), ...
        'report','image_result','input_path','cfg','-v7.3');
end
img_db_visual_thresholded = interp2(image_result.x_img,image_result.z_img, ...
    img_db_compensated,X_display,Z_display,'linear');
threshold_mask = img_db_compensated >= display_threshold_db;
if cfg.smooth_display_threshold_edges
    % 只细化二值边缘。面积约束禁止平滑后的显示区域比最近邻放大结果更大，
    % 因而不会因去网格化而把缺陷视觉加宽。
    threshold_mask_display = smooth_display_threshold_mask( ...
        threshold_mask,image_result.x_img,image_result.z_img, ...
        X_display,Z_display,cfg.display_edge_smoothing_sigma_px);
else
    threshold_mask_display = interp2( ...
        image_result.x_img,image_result.z_img,double(threshold_mask), ...
        X_display,Z_display,'nearest') >= 0.5;
end
thresholded_inside_db = min(max( ...
    img_db_visual_thresholded,display_threshold_db),0);
img_db_visual_thresholded(:) = cfg.display_background_db;
% 保持阈值区与背景之间的红蓝硬分界，不做颜色渐变或透明混合。
img_db_visual_thresholded(threshold_mask_display) = ...
    thresholded_inside_db(threshold_mask_display);

% 图1：未截断图（简洁样式）。
fig2 = figure('Color','w','Position',[120 100 900 700]);
imagesc(x_display,z_display,img_db_visual_full);
axis image; set(gca,'YDir','reverse'); colormap jet;
caxis([cfg.display_background_db 0]);
style_axis_clean(gca);
exportgraphics(fig2,fullfile(cfg.output_dir,'das_image_full.png'), ...
    'Resolution',300);

% 图2：深度补偿 + 阈值截断图（简洁样式）。
fig3 = figure('Color','w','Position',[160 120 900 700]);
imagesc(x_display,z_display,img_db_visual_thresholded);
axis image; set(gca,'YDir','reverse'); colormap jet;
caxis([cfg.display_background_db 0]);
style_axis_clean(gca);
exportgraphics(fig3,fullfile(cfg.output_dir,'das_image_thresholded.png'), ...
    'Resolution',300);

end


function style_axis_clean(ax)
% 简洁显示样式：仅保留图像与坐标轴刻度，去除标题/颜色条/坐标轴文字，
% 坐标轴刻度字体设为 Times New Roman。
set(ax,'FontName','Times New Roman','FontSize',11);
end


function mask_display = smooth_display_threshold_mask(mask,x,z,Xq,Zq,sigma_px)
% 在高分辨率网格上用有符号距离场细化阈值边缘；仅改变显示掩膜，
% 不改变成像或定量数据。每个连通域的面积及两个主轴方向投影尺寸
% 都不超过最近邻放大结果，避免平滑后视觉变宽或变长。
mask = logical(mask);
mask_display = false(size(Xq));
if ~any(mask(:)), return; end
if all(mask(:))
    mask_display(:) = true;
    return;
end
if nargin < 6 || isempty(sigma_px), sigma_px = 1.2; end

components = bwconncomp(mask,8);
for k = 1:components.NumObjects
    component = false(size(mask));
    component(components.PixelIdxList{k}) = true;

    % 先在线性插值后的高分辨率网格上重建轮廓，再计算距离场。
    % 这样斜边不再继承原始低分辨率掩膜的方格台阶。
    component_high_res = interp2( ...
        x,z,double(component),Xq,Zq,'linear',0) >= 0.5;
    if ~any(component_high_res(:)), continue; end
    distance_display = bwdist(~component_high_res) - ...
        bwdist(component_high_res);
    if sigma_px > 0
        distance_display = imgaussfilt( ...
            distance_display,sigma_px,'Padding','replicate');
    end

    % 最近邻结果定义原始显示面积和尺寸上限。
    nearest_display = interp2( ...
        x,z,double(component),Xq,Zq,'nearest') >= 0.5;
    target_count = nnz(nearest_display);
    if target_count == 0, continue; end

    [target_row,target_col] = find(nearest_display);
    target_points = [Xq(sub2ind(size(Xq),target_row,target_col)), ...
        Zq(sub2ind(size(Zq),target_row,target_col))];
    target_center = mean(target_points,1);
    centered_target = target_points-target_center;
    if size(centered_target,1) >= 2
        [basis,~] = eig(cov(centered_target));
    else
        basis = eye(2);
    end
    target_projection = centered_target*basis;
    target_span = max(target_projection,[],1)-min(target_projection,[],1);

    % 在距离场的等值线中寻找最外层且满足面积、长度和宽度上限的轮廓。
    % 最终结果仍是逻辑掩膜，所以红蓝区域之间不会出现渐变色。
    level_low = 0;
    level_high = max(distance_display(:));
    component_display = distance_display >= level_low;
    if ~display_mask_within_limits(component_display,target_count, ...
            target_center,basis,target_span,Xq,Zq)
        for iteration = 1:20
            level_mid = (level_low+level_high)/2;
            test_display = distance_display >= level_mid;
            if display_mask_within_limits(test_display,target_count, ...
                    target_center,basis,target_span,Xq,Zq)
                level_high = level_mid;
            else
                level_low = level_mid;
            end
        end
        component_display = distance_display >= level_high;
    end
    mask_display = mask_display | component_display;
end
end


function fits = display_mask_within_limits(candidate,target_count, ...
        target_center,basis,target_span,Xq,Zq)
fits = nnz(candidate) <= target_count;
if ~fits, return; end
[row,col] = find(candidate);
if isempty(row), return; end
points = [Xq(sub2ind(size(Xq),row,col)), ...
    Zq(sub2ind(size(Zq),row,col))];
projection = (points-target_center)*basis;
span = max(projection,[],1)-min(projection,[],1);
fits = all(span <= target_span+eps);
end


function draw_defect_annotations(ax, report)
% 在B扫图上叠加缺陷标注：裂纹/未熔合画线段，气孔画圆，统一黑色，
% 并标注缺陷类型文字（同类多个时加序号）。
if isempty(report) || height(report) == 0
    return;
end
hold(ax,'on');
black = [0 0 0];
types = report.Type;
[~,~,ic] = unique(types);
total_per_type = accumarray(ic,1);
seen_per_type = zeros(size(total_per_type));
for k = 1:height(report)
    type = types{k};
    cx = report.CenterX_mm(k);
    cz = report.CenterZ_mm(k);
    if strcmp(type,'Pore')
        d = report.Diameter_mm(k);
        if isfinite(d) && d > 0
            r = d/2;
            theta = linspace(0,2*pi,120);
            plot(ax,cx+r*cos(theta),cz+r*sin(theta), ...
                '-','Color',black,'LineWidth',1.5);
        end
        label_offset_z = -(max(d,1)/2 + 1.5);
    else
        len = report.CorrectedLength_mm(k);
        if ~isfinite(len) || len <= 0
            len = report.RawLength_mm(k);
        end
        ang = report.ImageAngle_deg(k);
        if ~isfinite(ang), ang = 0; end
        dx = (len/2)*cosd(ang);
        dz = (len/2)*sind(ang);
        plot(ax,[cx-dx cx+dx],[cz-dz cz+dz], ...
            '-','Color',black,'LineWidth',2);
        label_offset_z = -(max(len,1)/2 + 1.5);
    end
    seen_per_type(ic(k)) = seen_per_type(ic(k)) + 1;
    if total_per_type(ic(k)) > 1
        label = sprintf('%s %d',type,seen_per_type(ic(k)));
    else
        label = type;
    end
    text(ax,cx,cz+label_offset_z,label, ...
        'Color',black,'FontName','Times New Roman','FontSize',11, ...
        'HorizontalAlignment','center');
end
hold(ax,'off');
end


function delete_if_exists(path)
if isfile(path), delete(path); end
end


function img_db = depth_compensated_display_db( ...
        envelope,z_img,model,db_floor,normalization_z_range_mm)
if isfield(model,'model_family') && strcmp(model.model_family,'hybrid_v22_v26')
    % 混合模型的显示深度补偿沿用稳定的 V2.2 小孔径分支。
    img_db = depth_compensated_display_db( ...
        envelope,z_img,model.hybrid_small,db_floor,normalization_z_range_mm);
    return;
elseif isempty(fieldnames(model)) || ~isfield(model,'processing_config')
    warning('深度补偿模型不可用，第二张图退回未补偿显示。');
    compensated = envelope;
elseif isfield(model,'model_family') && strcmp(model.model_family,'v22_monotonic')
    cfg=model.processing_config;
    depth=(z_img(:)-cfg.depth_reference_mm)/cfg.new_v22_depth_scale_mm;
    % V2.2训练时 corrected_log = raw_log-linear*d-quadratic*d^2。
    gain=exp(-model.peak_depth_linear*depth-model.peak_depth_quadratic*depth.^2);
    max_gain=10^(cfg.max_depth_gain_db/20);
    gain=min(max(gain,1/max_gain),max_gain);
    compensated=bsxfun(@times,envelope,gain);
elseif isfield(model,'depth_slope_log_amplitude_per_mm') && ...
        isfield(model,'depth_reference_mm')
    gain = exp(-model.depth_slope_log_amplitude_per_mm* ...
        (z_img(:)-model.depth_reference_mm));
    max_gain = 10^(model.processing_config.max_depth_gain_db/20);
    gain = min(max(gain,1/max_gain),max_gain);
    compensated = bsxfun(@times,envelope,gain);
else
    warning('模型中没有可用的深度补偿系数，第二张图退回未补偿显示。');
    compensated=envelope;
end
normalization_peak = max(compensated(:));
if nargin >= 5 && ~isempty(normalization_z_range_mm)
    z_normalization_mask = z_img >= normalization_z_range_mm(1) & ...
        z_img <= normalization_z_range_mm(2);
    if any(z_normalization_mask)
        normalization_region = compensated(z_normalization_mask,:);
        candidate_peak = max(normalization_region(:));
        if isfinite(candidate_peak) && candidate_peak > 0
            normalization_peak = candidate_peak;
        end
    end
end
img_db = 20*log10(compensated/max(normalization_peak,realmin)+eps);
img_db(img_db < db_floor) = db_floor;
end


function report = empty_report()
report = table('Size',[0 33], ...
    'VariableTypes',{'double','cell','cell','cell','double','cell', ...
    'double','double','double','double','double','double','double','double', ...
    'double','double','double','double','double','double','double','double', ...
    'double','double','double','double','double','double','double','double', ...
    'double','cell','cell'}, ...
    'VariableNames',{'DefectID','Type','ClassificationMethod','LOFSlagSuggestion', ...
    'SlagProbability','LOFSlagSuggestionStatus','CrackProbability', ...
    'CNNThreshold','CenterX_mm','CenterZ_mm','PeakX_mm','PeakZ_mm','GlobalPeak_dB', ...
    'RawLength_mm','CorrectedLength_mm','Diameter_mm','Area_mm2', ...
    'MinorAxisNominal_mm','MinorAxisLower_mm','MinorAxisUpper_mm', ...
    'AreaLower_mm2','AreaUpper_mm2','AdvisorySlagMajor_mm', ...
    'AdvisorySlagShortLower_mm','AdvisorySlagShortUpper_mm', ...
    'AdvisorySlagArea_mm2','AdvisorySlagAreaLower_mm2', ...
    'AdvisorySlagAreaUpper_mm2','ImageAngle_deg', ...
    'AspectRatio','SNR_dB','QuantificationStatus','Warning'});
end
