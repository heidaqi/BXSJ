function result = Ascan(data_folder, save_folder, params_json)
use_environment = nargin < 3;

% ============================================================
% 0. 路径：优先使用环境变量（Python Service 调用时设置）
%    如果环境变量不存在，回退到硬编码默认值（方便单独调试）
% ============================================================

if nargin < 1 || isempty(data_folder)
    data_folder = getenv('MATLAB_DATA_DIR');
end
if isempty(data_folder)
    data_folder = '.\data\d0.5mm(0.06,0.02)d1mm(0.055,0.022)d0.6mm(0.054,0.016)';
end

if nargin < 2 || isempty(save_folder)
    save_folder = getenv('MATLAB_OUTPUT_DIR');
end
if isempty(save_folder)
    save_folder = '.\res';
end

if nargin < 3 || isempty(params_json)
    params = struct();
elseif isstruct(params_json)
    params = params_json;
else
    params = jsondecode(char(params_json));
end
if ~use_environment
    params.direct_call = true;
end

fprintf('\n============================================\n');
fprintf('A 扫分析\n');
fprintf('数据目录：%s\n', data_folder);
fprintf('输出目录：%s\n', save_folder);
fprintf('============================================\n');

%% ============================================================
% 1. Tx / Rx位置
%% ============================================================
env_tx = get_param_text(params, 'tx_positions_mm', 'MATLAB_TX_POSITIONS_MM');
if ~isempty(env_tx)
    tx_positions = str2double(strsplit(env_tx, ','));
else
    tx_positions = [30 32 34 36 38 40 42 44 49 51 53 55 57 59 61 63];
end
env_rx = get_param_text(params, 'rx_positions_mm', 'MATLAB_RX_POSITIONS_MM');
if ~isempty(env_rx)
    rx_positions = str2double(strsplit(env_rx, ','));
else
    rx_positions = tx_positions;
end

% 数字文件名是旧版COMSOL格式中最可靠的Tx物理坐标。即使界面传入了
% 通用默认阵元表，也优先采用数据自身的坐标；Rx列与Tx数量相同时按同阵列处理。
[detected_positions, position_source] = infer_probe_positions(data_folder);
if isempty(env_tx) && ~isempty(detected_positions)
    tx_positions = detected_positions;
    if isempty(env_rx)
        rx_positions = detected_positions;
    end
elseif ~isempty(env_tx)
    position_source = 'explicit_manifest_or_parameter';
else
    position_source = 'configured_or_default';
end
fprintf('阵元配置来源: %s\n', position_source);
fprintf('Tx/Rx坐标(mm):'); fprintf(' %.3g', tx_positions); fprintf('\n');

%% ============================================================
% 2. 多孔径Tx-Rx组合（默认64组）
% 每个Tx选择同阵元、1/4、1/2、3/4阵列跨度的Rx，兼顾近孔径和远孔径，
% 避免原来8组左右对称通道造成横向定位模糊。
%% ============================================================
n_element = min(length(tx_positions), length(rx_positions));
aperture_offsets = unique([0, round(n_element/4), round(n_element/2), round(3*n_element/4)]);
pair_tx = [];
pair_rx = [];
for tx_index = 1:n_element
    for offset = aperture_offsets
        rx_index = mod(tx_index-1+offset, n_element)+1;
        pair_tx(end+1) = tx_positions(tx_index); %#ok<AGROW>
        pair_rx(end+1) = rx_positions(rx_index); %#ok<AGROW>
    end
end
n_pair = length(pair_tx);

fprintf('\n============================================\n');
fprintf('       多孔径通道设置（%d组）\n', n_pair);
fprintf('============================================\n');
for p = 1:min(n_pair, 16)
    fprintf('%d : Tx = %d mm, Rx = %d mm, PCS = %d mm\n', ...
        p, pair_tx(p), pair_rx(p), abs(pair_rx(p)-pair_tx(p)));
end
if n_pair > 16
    fprintf('其余 %d 组通道省略显示。\n', n_pair-16);
end

%% ============================================================
% 3. 材料参数（优先读取环境变量）
%% ============================================================
env_velocity = get_param_text(params, 'velocity_mps', 'MATLAB_VELOCITY_MPS');
if ~isempty(env_velocity)
    velocity_mps = str2double(env_velocity);  % m/s
else
    velocity_mps = 5900;
end
velocity_mmpers = velocity_mps * 1000;  % mm/s

%% ============================================================
% 4. 板厚（优先读取环境变量）
%% ============================================================
env_thickness = get_param_text(params, 'plate_thickness_mm', 'MATLAB_PLATE_THICKNESS_MM');
if ~isempty(env_thickness)
    plate_thickness_mm = str2double(env_thickness);
else
    plate_thickness_mm = 40;
end
t_bottom_us = 2 * plate_thickness_mm / velocity_mmpers * 1e6;
fprintf('\n板厚 = %.1f mm, 底面回波理论时间 = %.2f μs\n', plate_thickness_mm, t_bottom_us);

%% ============================================================
% 5. 检测参数
%% ============================================================
min_peak_prominence_ratio = get_param_number(params, 'ascan_min_prominence_ratio', 0.04);
noise_sigma_multiplier = get_param_number(params, 'ascan_noise_sigma_multiplier', 6.0);
min_peak_distance_us = 0.20;
max_events_per_a_scan = 10;
direct_exclude_after_us = 0.30;
search_tolerance_us = 0.40;

% 搜索范围（优先读取环境变量）
env_x_min = get_param_text(params, 'x_min_mm', 'MATLAB_X_MIN_MM');
env_x_max = get_param_text(params, 'x_max_mm', 'MATLAB_X_MAX_MM');
if ~isempty(env_x_min) && ~isempty(env_x_max)
    x_range = [str2double(env_x_min) str2double(env_x_max)];
else
    x_range = [20 80];
end

env_z_min = get_param_text(params, 'z_min_mm', 'MATLAB_Z_MIN_MM');
env_z_max = get_param_text(params, 'z_max_mm', 'MATLAB_Z_MAX_MM');
if ~isempty(env_z_min) && ~isempty(env_z_max)
    z_range = [max(2, str2double(env_z_min)) str2double(env_z_max)];
else
    z_range = [2 38];
end

search_step = 0.5;
expected_point = [48, 22];

%% ============================================================
% 6. 读取数据（统一接口）
%% ============================================================
paut = load_data(data_folder, tx_positions, rx_positions);

data = paut.data;
time_axis = paut.time;
n_tx = paut.n_tx;
n_rx = paut.n_rx;
n_sample = paut.n_sample;
dt = paut.dt;
fs = paut.fs;

fprintf('\n数据读取完成：Tx=%d，Rx=%d，采样点=%d\n', n_tx, n_rx, n_sample);
fprintf('dt = %.3f ns, fs = %.2f MHz\n', dt*1e9, fs/1e6);

%% ============================================================
% 7. 提取8组对称A扫
%% ============================================================
pair_signal = cell(n_pair,1);
pair_env = cell(n_pair,1);
pair_time = cell(n_pair,1);

for p = 1:n_pair
    tx = pair_tx(p); rx = pair_rx(p);
    [~,tx_idx] = min(abs(tx_positions-tx));
    [~,rx_idx] = min(abs(rx_positions-rx));
    sig = data(:,tx_idx,rx_idx);
    sig = sig - mean(sig);
    env = abs(hilbert(sig));
    pair_signal{p} = sig;
    pair_env{p} = env;
    pair_time{p} = time_axis;
end


%% ============================================================
% 7.1 用同阵元Tx=Rx脉冲自动校正公共时间零点 t0
%% ============================================================
t0_candidates_us = nan(n_element, 1);
t0_search_end_us = get_param_number(params, 'ascan_t0_search_end_us', 3.0);
for element_index = 1:n_element
    diagonal_signal = data(:, element_index, element_index);
    diagonal_signal = diagonal_signal-mean(diagonal_signal);
    diagonal_env = abs(hilbert(diagonal_signal));
    t_raw_us = time_axis*1e6;
    mask_t0 = t_raw_us >= 0 & t_raw_us <= t0_search_end_us;
    if any(mask_t0)
        indices = find(mask_t0);
        [~, local_index] = max(diagonal_env(mask_t0));
        observed_time_us = t_raw_us(indices(local_index));
        t0_candidates_us(element_index) = observed_time_us;
    end
end
valid_t0 = t0_candidates_us(isfinite(t0_candidates_us));
if isempty(valid_t0)
    t0_offset_us = 0;
    t0_status = 'fallback_zero';
    t0_inlier_count = 0;
    t0_spread_us = NaN;
else
    t0_median = median(valid_t0);
    t0_mad = median(abs(valid_t0-t0_median));
    tolerance = max(0.10, 3*1.4826*t0_mad);
    inliers = valid_t0(abs(valid_t0-t0_median) <= tolerance);
    t0_offset_us = median(inliers);
    t0_status = 'diagonal_pulse_auto';
    t0_inlier_count = numel(inliers);
    t0_spread_us = median(abs(inliers-t0_offset_us));
end
t0_quality = (t0_inlier_count/max(n_element, 1)) * ...
    exp(-max(t0_spread_us, 0)/0.20);
if ~isfinite(t0_quality)
    t0_quality = 0;
end
fprintf('t0校正: %.4f us (%s, %d/%d通道)\n', ...
    t0_offset_us, t0_status, sum(isfinite(t0_candidates_us)), n_element);

%% ============================================================
% 8. 事件检测
%% ============================================================
events = cell(n_pair,1);

for p = 1:n_pair
    tx = pair_tx(p);
    rx = pair_rx(p);
    env = pair_env{p};
    t_us = time_axis * 1e6 - t0_offset_us;
    
    direct_time_us = abs(tx-rx) / velocity_mmpers * 1e6;
    t_start = direct_time_us + direct_exclude_after_us;
    t_end = min(t_bottom_us + 2.0, max(t_us));
    mask = (t_us >= t_start) & (t_us <= t_end);
    t_search = t_us(mask);
    env_search = env(mask);
    
    env_smooth = smoothdata(env_search, 'movmean', 5);
    max_env = max(env_smooth);
    if max_env <= 0
        events{p} = [];
        continue;
    end
    
    % 用当前通道搜索窗的稳健噪声统计确定阈值，避免固定2%%阈值把旁瓣和
    % 数值噪声当成缺陷。MAD对少量强回波不敏感。
    noise_median = median(env_smooth);
    noise_sigma = 1.4826 * median(abs(env_smooth-noise_median));
    min_prom = max(min_peak_prominence_ratio * max_env, ...
        noise_sigma_multiplier * max(noise_sigma, eps));
    min_height = noise_median + noise_sigma_multiplier * max(noise_sigma, eps);
    min_peak_distance_samples = max(1, round(min_peak_distance_us*1e-6/dt));
    [pks, locs, ~, prominences] = findpeaks(env_smooth, ...
        'MinPeakProminence', min_prom, ...
        'MinPeakHeight', min_height, ...
        'MinPeakDistance', min_peak_distance_samples);
    
    if isempty(pks)
        events{p} = [];
        continue;
    end
    
    peak_times = t_search(locs);
    % 只保留显著度最高的事件，再按时间排序；不能简单取最早的10个峰。
    [~, strength_order] = sort(pks, 'descend');
    n_keep = min(max_events_per_a_scan, length(pks));
    strength_order = strength_order(1:n_keep);
    peak_times = peak_times(strength_order);
    pks = pks(strength_order);
    prominences = prominences(strength_order);
    [peak_times, time_order] = sort(peak_times);
    pks = pks(time_order);
    prominences = prominences(time_order);
    
    ev = [];
    for k = 1:length(peak_times)
        snr_db = 20*log10(max(pks(k), eps) / max(noise_median+noise_sigma, eps));
        normalized_prominence = prominences(k) / max(max_env, eps);
        temp = struct('time_us', peak_times(k), 'amp', pks(k), ...
            'snr_db', snr_db, 'prominence_ratio', normalized_prominence);
        ev = [ev; temp];
    end
    events{p} = ev;
    
    if p <= 16
        fprintf('\n%d: Tx=%d -> Rx=%d, 直达波理论 %.3f μs\n', p, tx, rx, direct_time_us);
        for k = 1:length(ev)
            fprintf('  事件%d: t=%.3f μs, amp=%.4f\n', k, ev(k).time_us, ev(k).amp);
        end
    end
end

%% ============================================================
% 9. 搜索网格
%% ============================================================
x_grid = x_range(1):search_step:x_range(2);
z_grid = z_range(1):search_step:z_range(2);
n_x = length(x_grid);
n_z = length(z_grid);

fprintf('\n搜索网格：\n');
fprintf('X范围：%.1f ~ %.1f mm，步长 %.2f mm，共 %d 点\n', ...
    x_range(1), x_range(2), search_step, n_x);
fprintf('Z范围：%.1f ~ %.1f mm，步长 %.2f mm，共 %d 点\n', ...
    z_range(1), z_range(2), search_step, n_z);

tof_predict = @(x, z, tx, rx) ...
    (sqrt((x-tx)^2 + z^2) + sqrt((x-rx)^2 + z^2)) / velocity_mmpers * 1e6;

fprintf('\n============================================\n');
fprintf('       多点迭代全局搜索\n');
fprintf('============================================\n');

% 诊断试验C：主候选完全保持原始基线，再从剩余空间追加弱候选。
% 弱候选不参与主候选排序，因此不会挤掉原来稳定的20个结果。
max_primary_points = 20;
max_secondary_points = 10;
min_score = 0.45;
min_support = max(8, ceil(0.20*n_pair));
secondary_min_score = 0.45;
secondary_min_support = 8;
suppression_radius_mm = 3.0;

candidate_points = [];
candidate_score = [];
candidate_support = [];
candidate_error = [];
candidate_event_idx = cell(0);
candidate_residual_std = [];
candidate_mean_snr = [];
candidate_mean_prominence = [];
candidate_aperture_coverage = [];
candidate_direct_distance = [];
candidate_backwall_distance = [];
candidate_mean_amplitude = [];
candidate_tier = strings(0,1);

% 一次性计算完整TOF得分图。后续候选提取不再删除任何通道事件，
% 只在空间上抑制已选峰周围区域，避免贪心顺序污染后续缺陷。
score_map = zeros(n_z, n_x);
support_map = zeros(n_z, n_x);
error_map = inf(n_z, n_x);
for iz = 1:n_z
    z = z_grid(iz);
    for ix = 1:n_x
        x = x_grid(ix);
        total_err = 0;
        support = 0;
        for p = 1:n_pair
            tx = pair_tx(p);
            rx = pair_rx(p);
            t_pred = tof_predict(x, z, tx, rx);
            ev = events{p};
            if isempty(ev)
                continue;
            end
            err = abs([ev.time_us]-t_pred);
            min_err = min(err);
            if min_err <= search_tolerance_us
                total_err = total_err+min_err;
                support = support+1;
            end
        end
        if support > 0
            avg_err = total_err/support;
            score_map(iz,ix) = min(1, support/min_support)*exp(-avg_err/search_tolerance_us);
            support_map(iz,ix) = support;
            error_map(iz,ix) = avg_err;
        end
    end
end
[x_mesh, z_mesh] = meshgrid(x_grid, z_grid);
available_mask = true(n_z, n_x);

% 第二层使用不受主层support/13惩罚的时间一致性得分，但要求至少8个通道。
secondary_score_map = exp(-error_map/search_tolerance_us);
secondary_score_map(~isfinite(error_map) | support_map < secondary_min_support) = 0;
secondary_phase = false;
primary_count = 0;
secondary_count = 0;
iteration = 0;
while true
    if ~secondary_phase && primary_count >= max_primary_points
        secondary_phase = true;
    end
    if secondary_phase && secondary_count >= max_secondary_points
        break;
    end
    iteration = iteration + 1;
    fprintf('\n--------------------------------------------\n');
    if secondary_phase
        fprintf('弱候选层第 %d 个局部极大值\n', secondary_count+1);
        active_score_map = secondary_score_map;
        active_min_score = secondary_min_score;
        active_min_support = secondary_min_support;
    else
        fprintf('主候选层第 %d 个局部极大值\n', primary_count+1);
        active_score_map = score_map;
        active_min_score = min_score;
        active_min_support = min_support;
    end
    fprintf('--------------------------------------------\n');
    selection_map = active_score_map;
    selection_map(~available_mask) = 0;
    [max_score, idx_max] = max(selection_map(:));
    if max_score <= 0
        fprintf('没有找到任何可匹配的候选点。\n');
        if ~secondary_phase
            secondary_phase = true;
            continue;
        end
        break;
    end

    [iz_best, ix_best] = ind2sub(size(score_map), idx_max);
    point = [x_grid(ix_best), z_grid(iz_best)];
    support = support_map(iz_best, ix_best);
    avg_err = error_map(iz_best, ix_best);

    fprintf('候选点：X=%.2f mm, Z=%.2f mm\n', point(1), point(2));
    fprintf('Score = %.4f\n', max_score);
    fprintf('Support = %d/%d\n', support, n_pair);
    fprintf('平均TOF误差 = %.3f μs\n', avg_err);

    if max_score < active_min_score
        fprintf('\n最高Score已经低于本层阈值 %.2f\n', active_min_score);
        if ~secondary_phase
            secondary_phase = true;
            continue;
        end
        break;
    end

    if support < active_min_support
        fprintf('\n支撑通道只有 %d/%d\n', support, n_pair);
        fprintf('低于本层最低要求 %d。\n', active_min_support);
        if ~secondary_phase
            secondary_phase = true;
            continue;
        end
        break;
    end

    candidate_points = [candidate_points; point];
    % CSV中的Score始终保留基线定义，保证新旧数据和既有模型可比较。
    candidate_score = [candidate_score; score_map(iz_best,ix_best)];
    candidate_support = [candidate_support; support];
    candidate_error = [candidate_error; avg_err];
    if secondary_phase
        secondary_count = secondary_count + 1;
        candidate_tier(end+1,1) = "secondary";
    else
        primary_count = primary_count + 1;
        candidate_tier(end+1,1) = "primary";
    end

    matched_event_idx = nan(n_pair,1);
    matched_residuals = [];
    matched_snr = [];
    matched_prominence = [];
    matched_amplitude = [];
    matched_sensor_positions = [];
    matched_direct_distance = [];
    matched_backwall_distance = [];
    for p = 1:n_pair
        tx = pair_tx(p);
        rx = pair_rx(p);
        t_pred = tof_predict(point(1), point(2), tx, rx);
        ev = events{p};
        if isempty(ev)
            continue;
        end
        err = abs([ev.time_us] - t_pred);
        [min_err, idx] = min(err);
        if min_err <= search_tolerance_us
            matched_event_idx(p) = idx;
            matched_residuals(end+1) = min_err; %#ok<AGROW>
            matched_snr(end+1) = ev(idx).snr_db; %#ok<AGROW>
            matched_prominence(end+1) = ev(idx).prominence_ratio; %#ok<AGROW>
            matched_amplitude(end+1) = ev(idx).amp; %#ok<AGROW>
            matched_sensor_positions = [matched_sensor_positions tx rx]; %#ok<AGROW>
            direct_time = abs(tx-rx)/velocity_mmpers*1e6;
            matched_direct_distance(end+1) = max(0, t_pred-direct_time); %#ok<AGROW>
            matched_backwall_distance(end+1) = abs(t_pred-t_bottom_us); %#ok<AGROW>
        end
    end
    candidate_event_idx{end+1} = matched_event_idx;
    candidate_residual_std(end+1,1) = safe_std(matched_residuals);
    candidate_mean_snr(end+1,1) = safe_mean(matched_snr);
    candidate_mean_prominence(end+1,1) = safe_mean(matched_prominence);
    full_aperture = max([tx_positions rx_positions])-min([tx_positions rx_positions]);
    if isempty(matched_sensor_positions) || full_aperture <= 0
        candidate_aperture_coverage(end+1,1) = 0;
    else
        candidate_aperture_coverage(end+1,1) = ...
            (max(matched_sensor_positions)-min(matched_sensor_positions))/full_aperture;
    end
    candidate_direct_distance(end+1,1) = safe_min(matched_direct_distance);
    candidate_backwall_distance(end+1,1) = safe_min(matched_backwall_distance);
    candidate_mean_amplitude(end+1,1) = safe_mean(matched_amplitude);
    available_mask((x_mesh-point(1)).^2+(z_mesh-point(2)).^2 < suppression_radius_mm^2) = false;
    fprintf('已保留原始通道事件，并抑制该点周围 %.1f mm 区域。\n', suppression_radius_mm);

    fprintf('\n当前已经找到 %d 个候选点：\n', size(candidate_points,1));
    for k = 1:size(candidate_points,1)
        fprintf('点%d：X=%.2f mm, Z=%.2f mm, Score=%.3f, Support=%d/%d\n', ...
            k, candidate_points(k,1), candidate_points(k,2), ...
            candidate_score(k), candidate_support(k), n_pair);
    end
end

%% ============================================================
% 10. 最终结果输出：保存到文件
%% ============================================================
fprintf('\n============================================\n');
fprintf('       检测到的候选点位置\n');
fprintf('============================================\n');

n_candidates = size(candidate_points, 1);

if n_candidates == 0
    fprintf('未检测到任何候选点。\n');
else
    for k = 1:n_candidates
        fprintf('候选点%d：X = %.2f mm, Z = %.2f mm\n', ...
            k, candidate_points(k,1), candidate_points(k,2));
    end
end

% ----- 真值分阶段诊断（仅在测试批处理传入真值时启用）-----
diagnostic_csv_path = '';
diagnostic_image_path = '';
if ~exist(save_folder, 'dir')
    mkdir(save_folder);
end
if isfield(params, 'diagnostic_truth_regions') && ~isempty(params.diagnostic_truth_regions)
    truth_regions = params.diagnostic_truth_regions;
    truth_tolerance_mm = get_param_number(params, 'diagnostic_tolerance_mm', 2.0);
    n_truth = numel(truth_regions);
    truth_index_column = zeros(n_truth,1);
    truth_type_column = strings(n_truth,1);
    best_x_column = nan(n_truth,1);
    best_z_column = nan(n_truth,1);
    best_score_column = zeros(n_truth,1);
    best_support_column = zeros(n_truth,1);
    best_support_ratio_column = zeros(n_truth,1);
    best_error_column = nan(n_truth,1);
    mean_predicted_tof_column = nan(n_truth,1);
    mean_matched_snr_column = nan(n_truth,1);
    nearest_candidate_distance_column = nan(n_truth,1);
    detected_column = zeros(n_truth,1);
    failure_stage_column = strings(n_truth,1);

    for truth_row = 1:n_truth
        region = truth_regions(truth_row);
        truth_index_column(truth_row) = double(region.truth_index);
        truth_type_column(truth_row) = string(region.type);
        distance_map = truth_distance_map(x_mesh, z_mesh, region);
        region_mask = distance_map <= truth_tolerance_mm;
        if ~any(region_mask(:))
            failure_stage_column(truth_row) = "outside_search_grid";
            continue;
        end
        regional_scores = score_map;
        regional_scores(~region_mask) = -Inf;
        [best_score, best_linear_index] = max(regional_scores(:));
        [best_iz, best_ix] = ind2sub(size(score_map), best_linear_index);
        best_x = x_grid(best_ix);
        best_z = z_grid(best_iz);
        best_support = support_map(best_iz,best_ix);
        best_error = error_map(best_iz,best_ix);
        best_x_column(truth_row) = best_x;
        best_z_column(truth_row) = best_z;
        best_score_column(truth_row) = max(0,best_score);
        best_support_column(truth_row) = best_support;
        best_support_ratio_column(truth_row) = best_support/max(n_pair,1);
        best_error_column(truth_row) = best_error;

        predicted_tofs = nan(n_pair,1);
        matched_snrs = [];
        for p = 1:n_pair
            predicted_tofs(p) = tof_predict(best_x,best_z,pair_tx(p),pair_rx(p));
            ev = events{p};
            if isempty(ev), continue; end
            [event_error,event_index] = min(abs([ev.time_us]-predicted_tofs(p)));
            if event_error <= search_tolerance_us
                matched_snrs(end+1) = ev(event_index).snr_db; %#ok<AGROW>
            end
        end
        mean_predicted_tof_column(truth_row) = mean(predicted_tofs,'omitnan');
        mean_matched_snr_column(truth_row) = safe_mean(matched_snrs);

        if n_candidates > 0
            candidate_distances = truth_point_distances(candidate_points(:,1),candidate_points(:,2),region);
            nearest_candidate_distance_column(truth_row) = min(candidate_distances);
            detected_column(truth_row) = any(candidate_distances <= truth_tolerance_mm);
        end
        if detected_column(truth_row)
            failure_stage_column(truth_row) = "detected";
        elseif best_support == 0
            failure_stage_column(truth_row) = "no_detected_events_near_truth";
        elseif best_support < min_support
            failure_stage_column(truth_row) = "support_below_minimum";
        elseif best_score < min_score
            failure_stage_column(truth_row) = "score_below_minimum";
        else
            failure_stage_column(truth_row) = "spatial_nms_or_candidate_limit";
        end
    end

    diagnostic_table = table(truth_index_column,truth_type_column,best_x_column,best_z_column, ...
        best_score_column,best_support_column,best_support_ratio_column,best_error_column, ...
        mean_predicted_tof_column,mean_matched_snr_column,nearest_candidate_distance_column, ...
        detected_column,failure_stage_column, ...
        'VariableNames',{'TruthIndex','TruthType','BestX_mm','BestZ_mm','BestScore', ...
        'BestSupport','BestSupportRatio','BestAvgError_us','MeanPredictedTOF_us', ...
        'MeanMatchedSNR_dB','NearestCandidateDistance_mm','Detected','FailureStage'});
    diagnostic_csv_path = fullfile(save_folder,'truth_diagnostics.csv');
    writetable(diagnostic_table,diagnostic_csv_path);

    diagnostic_image_path = fullfile(save_folder,'truth_score_map.png');
    diagnostic_figure = figure('Visible','off','Color','w');
    imagesc(x_grid,z_grid,score_map); axis xy; colorbar; hold on;
    plot(best_x_column,best_z_column,'wo','MarkerSize',9,'LineWidth',1.5);
    if n_candidates > 0
        plot(candidate_points(:,1),candidate_points(:,2),'r+','MarkerSize',9,'LineWidth',1.5);
    end
    xlabel('X (mm)'); ylabel('Z (mm)'); title('Truth-region score map (white: truth best, red: candidate)');
    exportgraphics(diagnostic_figure,diagnostic_image_path,'Resolution',160);
    close(diagnostic_figure);
end

% ----- 保存结果到文件（供 Python 读取）-----
if ~exist(save_folder, 'dir')
    mkdir(save_folder);
end

% 保存候选点信息到 MAT 文件
save(fullfile(save_folder, 'ascan_results.mat'), ...
    'candidate_points', 'candidate_score', 'candidate_support', ...
    'candidate_error', 'candidate_residual_std', 'candidate_mean_snr', ...
    'candidate_mean_prominence', 'candidate_aperture_coverage', ...
    'candidate_direct_distance', 'candidate_backwall_distance', ...
    'candidate_mean_amplitude', 'candidate_event_idx', 't0_quality', ...
    'candidate_tier', 'n_candidates', 'pair_tx', 'pair_rx');

calibration = struct('position_source', position_source, ...
    'tx_positions_mm', tx_positions, 'rx_positions_mm', rx_positions, ...
    'velocity_mps', velocity_mps, 't0_offset_us', t0_offset_us, ...
    't0_status', t0_status, 't0_candidates_us', t0_candidates_us, ...
    't0_inlier_count', t0_inlier_count, 't0_spread_us', t0_spread_us, ...
    't0_quality', t0_quality, ...
    'noise_sigma_multiplier', noise_sigma_multiplier, ...
    'min_peak_prominence_ratio', min_peak_prominence_ratio, ...
    'min_score', min_score, 'min_support', min_support, ...
    'secondary_min_score', secondary_min_score, ...
    'secondary_min_support', secondary_min_support, ...
    'max_primary_points', max_primary_points, ...
    'max_secondary_points', max_secondary_points, ...
    'candidate_search', 'score_map_spatial_nms', ...
    'suppression_radius_mm', suppression_radius_mm);
calibration_file = fullfile(save_folder, 'ascan_calibration.json');
fid = fopen(calibration_file, 'w');
if fid >= 0
    fprintf(fid, '%s', jsonencode(calibration));
    fclose(fid);
end

% 同时保存为 CSV，方便 Python 直接读取（更通用）
if n_candidates > 0
    support_ratio = candidate_support / max(n_pair, 1);
    t0_quality_column = repmat(t0_quality, n_candidates, 1);
    t0_offset_column = repmat(t0_offset_us, n_candidates, 1);
    T = table(candidate_points(:,1), candidate_points(:,2), ...
        candidate_score, candidate_support, candidate_error, support_ratio, ...
        candidate_residual_std, candidate_mean_snr, candidate_mean_prominence, ...
        candidate_aperture_coverage, candidate_direct_distance, ...
        candidate_backwall_distance, candidate_mean_amplitude, ...
        t0_quality_column, t0_offset_column, candidate_tier, ...
        'VariableNames', {'X_mm', 'Z_mm', 'Score', 'Support', 'AvgError_us', ...
        'SupportRatio', 'ResidualStd_us', 'MeanSNR_dB', 'MeanProminenceRatio', ...
        'ApertureCoverage', 'DistanceToDirectWave_us', 'DistanceToBackwall_us', ...
        'MeanMatchedAmplitude', 'T0Quality', 'T0Offset_us', 'CandidateTier'});
    writetable(T, fullfile(save_folder, 'ascan_results.csv'));
end

fprintf('\nA扫结果已保存到：%s\n', save_folder);
fprintf('==================== Ascan 完成 ====================\n');
result = struct('mat_path', fullfile(save_folder, 'ascan_results.mat'), ...
    'csv_path', fullfile(save_folder, 'ascan_results.csv'), ...
    'calibration_path', calibration_file, 'candidate_count', n_candidates, ...
    't0_offset_us', t0_offset_us, 'position_source', position_source, ...
    'diagnostic_csv_path', diagnostic_csv_path, ...
    'diagnostic_image_path', diagnostic_image_path);
end


function distances = truth_point_distances(x_values,z_values,region)
region_type = char(region.type);
if strcmp(region_type,'circle')
    distances = max(0,hypot(x_values-double(region.x1_mm),z_values-double(region.z1_mm))-double(region.radius_mm));
elseif strcmp(region_type,'segment')
    ax = double(region.x1_mm); az = double(region.z1_mm);
    bx = double(region.x2_mm); bz = double(region.z2_mm);
    dx = bx-ax; dz = bz-az; denominator = dx^2+dz^2;
    if denominator <= eps
        distances = hypot(x_values-ax,z_values-az);
    else
        ratio = max(0,min(1,((x_values-ax)*dx+(z_values-az)*dz)/denominator));
        distances = hypot(x_values-(ax+ratio*dx),z_values-(az+ratio*dz));
    end
elseif strcmp(region_type,'rectangle')
    half_width = double(region.x2_mm)/2; half_height = double(region.z2_mm)/2;
    distances = hypot(max(abs(x_values-double(region.x1_mm))-half_width,0), ...
        max(abs(z_values-double(region.z1_mm))-half_height,0));
else
    distances = inf(size(x_values));
end
end


function distances = truth_distance_map(x_mesh,z_mesh,region)
distances = truth_point_distances(x_mesh,z_mesh,region);
end


function value = safe_mean(values)
if isempty(values)
    value = NaN;
else
    value = mean(values, 'omitnan');
end
end


function value = safe_std(values)
if numel(values) < 2
    value = 0;
else
    value = std(values, 0, 'omitnan');
end
end


function value = safe_min(values)
if isempty(values)
    value = NaN;
else
    value = min(values, [], 'omitnan');
end
end


function [positions, source] = infer_probe_positions(data_folder)
positions = [];
source = 'configured_or_default';
if ~isfolder(data_folder)
    return;
end
files = dir(fullfile(data_folder, '*.txt'));
values = [];
for index = 1:numel(files)
    [~, stem, ~] = fileparts(files(index).name);
    value = str2double(stem);
    if isfinite(value)
        values(end+1) = value; %#ok<AGROW>
    end
end
if numel(values) < 2
    return;
end
values = sort(unique(values));
% COMSOL旧格式文件名通常直接使用mm；若使用m，则自动换算为mm。
if max(abs(values)) < 1
    values = values * 1000;
end
positions = values;
source = 'numeric_tx_filenames_rx_same_array';
end


function value = get_param_number(params, field_name, default_value)
if isfield(params, field_name)
    value = double(params.(field_name));
    if ~isscalar(value) || ~isfinite(value)
        value = default_value;
    end
else
    value = default_value;
end
end


function value = get_param_text(params, field_name, env_name)
if isfield(params, field_name)
    raw = params.(field_name);
    if isnumeric(raw)
        value = strjoin(arrayfun(@(x) sprintf('%.15g', x), raw(:).', ...
            'UniformOutput', false), ',');
    elseif islogical(raw)
        value = char(string(raw));
    else
        value = char(raw);
    end
else
    if isfield(params, 'direct_call')
        value = '';
    else
        value = getenv(env_name);
    end
end
end
