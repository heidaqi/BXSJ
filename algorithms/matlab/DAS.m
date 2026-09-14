function result = DAS(data_folder, save_folder, params_json)
use_environment = nargin < 3;
% 禁止显示图形窗口（用于后台批量处理）
set(0, 'DefaultFigureVisible', 'off');
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
fprintf('DAS 成像\n');
fprintf('数据目录：%s\n', data_folder);
fprintf('输出目录：%s\n', save_folder);
fprintf('============================================\n');

% -------------------------- 1. 参数设置 --------------------------
env_tx = get_param_text(params, 'tx_positions_mm', 'MATLAB_TX_POSITIONS_MM');
if ~isempty(env_tx)
    tx_positions = str2double(strsplit(env_tx, ','));
else
    tx_positions = [30,32,34,36,38,40,42,44,49,51,53,55,57,59,61,63];
end
env_rx = get_param_text(params, 'rx_positions_mm', 'MATLAB_RX_POSITIONS_MM');
if ~isempty(env_rx)
    rx_positions = str2double(strsplit(env_rx, ','));
else
    rx_positions = tx_positions;
end

% 材料参数（优先读取环境变量，未设置时回退到默认值）
env_velocity = get_param_text(params, 'velocity_mps', 'MATLAB_VELOCITY_MPS');
if ~isempty(env_velocity)
    velocity_mps = str2double(env_velocity);
else
    velocity_mps = 5900;     % 钢中纵波声速 m/s
end

env_thickness = get_param_text(params, 'plate_thickness_mm', 'MATLAB_PLATE_THICKNESS_MM');
if ~isempty(env_thickness)
    plate_thickness = str2double(env_thickness);
else
    plate_thickness = 40;    % 板厚 mm
end

% 成像区域设置（单位mm，优先读取环境变量）
env_x_min = get_param_text(params, 'x_min_mm', 'MATLAB_X_MIN_MM');
env_x_max = get_param_text(params, 'x_max_mm', 'MATLAB_X_MAX_MM');
if ~isempty(env_x_min) && ~isempty(env_x_max)
    x_range = [str2double(env_x_min), str2double(env_x_max)];  % 横向成像范围
else
    x_range = [20, 80];
end

env_z_min = get_param_text(params, 'z_min_mm', 'MATLAB_Z_MIN_MM');
env_z_max = get_param_text(params, 'z_max_mm', 'MATLAB_Z_MAX_MM');
if ~isempty(env_z_min) && ~isempty(env_z_max)
    z_range = [str2double(env_z_min), str2double(env_z_max)];  % 深度成像范围
else
    z_range = [0, 40];
end

env_pixel = get_param_text(params, 'pixel_step_mm', 'MATLAB_PIXEL_STEP_MM');
if ~isempty(env_pixel)
    pixel_step_mm = str2double(env_pixel);  % 成像像素步长
else
    pixel_step_mm = 0.2;
end

fprintf('材料声速: %.0f m/s, 板厚: %.1f mm\n', velocity_mps, plate_thickness);
fprintf('成像范围: x=[%.1f, %.1f] mm, z=[%.1f, %.1f] mm, 步长 %.2f mm\n', ...
    x_range(1), x_range(2), z_range(1), z_range(2), pixel_step_mm);

% -------------------------- 2. 读取数据（统一接口）--------------------------
paut = load_data(data_folder, tx_positions, rx_positions);

data = paut.data;
time_axis = paut.time;
n_tx = paut.n_tx;
n_rx = paut.n_rx;
n_sample = paut.n_sample;
dt = paut.dt;
fs = paut.fs;

fprintf('发射数: %d, 接收数: %d\n', n_tx, n_rx);
fprintf('采样点数: %d\n', n_sample);

% -------------------------- 3. A扫预处理 --------------------------
gate_start_us = get_param_number(params, 'gate_start_us', ...
    'MATLAB_GATE_START_US', 0.8);
gate_end_us = get_param_number(params, 'gate_end_us', ...
    'MATLAB_GATE_END_US', 14.5);
gate_transition_us = get_param_number(params, 'gate_transition_us', ...
    'MATLAB_GATE_TRANSITION_US', 0.25);

if gate_transition_us <= 0 || gate_end_us <= gate_start_us
    error('DAS:InvalidPreprocessParams', '时间门控参数无效。');
end

t_samples_us = (0:n_sample-1)' * dt * 1e6;
time_gate = zeros(n_sample, 1);
idx_main = t_samples_us >= gate_start_us & t_samples_us <= gate_end_us;
time_gate(idx_main) = 1;
idx_rise = t_samples_us >= gate_start_us-gate_transition_us & ...
    t_samples_us < gate_start_us;
time_gate(idx_rise) = 0.5 * (1-cos(pi * ...
    (t_samples_us(idx_rise)-(gate_start_us-gate_transition_us)) / gate_transition_us));
idx_fall = t_samples_us > gate_end_us & ...
    t_samples_us <= gate_end_us+gate_transition_us;
time_gate(idx_fall) = 0.5 * (1+cos(pi * ...
    (t_samples_us(idx_fall)-gate_end_us) / gate_transition_us));

for tx_idx = 1:n_tx
    data_tx = squeeze(data(:,tx_idx,:));
    data_tx = bsxfun(@minus, data_tx, mean(data_tx, 1));
    data(:,tx_idx,:) = reshape(bsxfun(@times, data_tx, time_gate), ...
        n_sample, 1, n_rx);
end
fprintf('A扫预处理完成：去直流，%.2f-%.2f us 时间门控\n', ...
    gate_start_us, gate_end_us);

% -------------------------- 4. 生成成像网格 --------------------------
x_img = x_range(1):pixel_step_mm:x_range(2);
z_img = z_range(1):pixel_step_mm:z_range(2);
n_x = length(x_img);
n_z = length(z_img);
img = zeros(n_z, n_x);

fprintf('开始DAS成像，成像网格: %d × %d 像素...\n', n_z, n_x);
fprintf('总通道数: %d (发射%d × 接收%d)\n', n_tx * n_rx, n_tx, n_rx);

% -------------------------- 5. DAS延迟叠加（向量化 + 参数缓存） --------------------------
velocity_mmpers = velocity_mps * 1000; % 声速转 mm/s
tx_x_all = tx_positions;
rx_x_all = rx_positions;
% 默认使用普通 DAS；仅在参数 use_cf_das=true 或环境变量
% MATLAB_USE_CF_DAS=true 时启用相干因子加权。
use_cf_das = get_param_bool(params, 'use_cf_das', 'MATLAB_USE_CF_DAS', false);
cf_alpha = get_param_number(params, 'cf_alpha', 'MATLAB_CF_ALPHA', 0.2);
if cf_alpha < 0
    error('DAS:InvalidCFAlpha', 'CF alpha 不能小于0。');
end

% 缓存只保存与几何和采样参数有关的延时索引，不保存当前信号数据。
% 相同参数自动复用；阵元位置、网格、声速、dt 或采样点数变化时生成新文件。
env_cache_dir = get_param_text(params, 'das_cache_dir', 'MATLAB_DAS_CACHE_DIR');
if isempty(env_cache_dir)
    cache_folder = fullfile(prefdir, 'YoloInspection', 'das_cache');
else
    cache_folder = env_cache_dir;
end
if ~exist(cache_folder, 'dir')
    mkdir(cache_folder);
end

cache_signature = struct( ...
    'cache_version', 'nearest_sample_v1', ...
    'tx_positions_mm', tx_positions, ...
    'rx_positions_mm', rx_positions, ...
    'x_range_mm', x_range, ...
    'z_range_mm', z_range, ...
    'pixel_step_mm', pixel_step_mm, ...
    'velocity_mps', velocity_mps, ...
    'dt_seconds', dt, ...
    'n_sample', n_sample, ...
    'n_tx', n_tx, ...
    'n_rx', n_rx);
signature_json = jsonencode(cache_signature);
cache_key = md5_text(signature_json);
cache_file = fullfile(cache_folder, ['das_delay_', cache_key, '.mat']);
cache_hit = false;
if n_sample <= double(intmax('uint16'))
    index_class = 'uint16';
else
    index_class = 'uint32';
end

if exist(cache_file, 'file')
    try
        cached = load(cache_file, 'cache_signature', 'sample_idx_map');
        expected_size = [n_z, n_x, n_tx, n_rx];
        if isfield(cached, 'cache_signature') && ...
                isequal(cached.cache_signature, cache_signature) && ...
                isfield(cached, 'sample_idx_map') && ...
                isequal(size(cached.sample_idx_map), expected_size)
            sample_idx_map = cached.sample_idx_map;
            cache_hit = true;
        end
    catch ME
        warning('DAS:CacheReadFailed', '延时缓存读取失败，将重新生成：%s', ME.message);
    end
end

if cache_hit
    fprintf('已复用DAS延时缓存：%s\n', cache_file);
else
    fprintf('正在生成DAS延时缓存：%s\n', cache_file);
    [X_grid, Z_grid] = meshgrid(x_img, z_img);
    sample_idx_map = zeros(n_z, n_x, n_tx, n_rx, index_class);
    for tx_idx = 1:n_tx
        d_tx_grid = sqrt((X_grid - tx_x_all(tx_idx)).^2 + Z_grid.^2);
        for rx_idx = 1:n_rx
            d_rx_grid = sqrt((X_grid - rx_x_all(rx_idx)).^2 + Z_grid.^2);
            sample_grid = round(((d_tx_grid + d_rx_grid) / velocity_mmpers) / dt) + 1;
            valid_grid = sample_grid >= 1 & sample_grid <= n_sample;
            channel_map = zeros(n_z, n_x, index_class);
            channel_map(valid_grid) = cast(sample_grid(valid_grid), index_class);
            sample_idx_map(:,:,tx_idx,rx_idx) = channel_map;
        end
    end
    cache_metadata = struct( ...
        'created_at', datestr(now, 30), ...
        'signature_json', signature_json, ...
        'index_class', index_class, ...
        'cache_file', cache_file);
    save(cache_file, 'cache_signature', 'cache_metadata', 'sample_idx_map', '-v7.3');
    fprintf('DAS延时缓存已保存。\n');
end

% 每个通道一次处理整张网格。普通 DAS 模式不计算 CF 所需能量，
% 避免默认成像产生额外计算开销。
tic;
img_sum = zeros(n_z, n_x);
if use_cf_das
    energy_sum = zeros(n_z, n_x);
    valid_count = zeros(n_z, n_x);
end
for tx_idx = 1:n_tx
    for rx_idx = 1:n_rx
        channel_map = sample_idx_map(:,:,tx_idx,rx_idx);
        valid_grid = channel_map > 0;
        if ~any(valid_grid(:))
            continue;
        end
        signal = data(:,tx_idx,rx_idx);
        delayed_grid = zeros(n_z, n_x);
        delayed_grid(valid_grid) = signal(double(channel_map(valid_grid)));
        img_sum = img_sum + delayed_grid;
        if use_cf_das
            energy_sum = energy_sum + delayed_grid.^2;
            valid_count = valid_count + valid_grid;
        end
    end
    if mod(tx_idx, 4) == 0 || tx_idx == n_tx
        fprintf('  已完成发射通道: %d/%d\n', tx_idx, n_tx);
    end
end
if use_cf_das
    cf = zeros(n_z, n_x);
    has_valid_data = valid_count > 0;
    cf(has_valid_data) = img_sum(has_valid_data).^2 ./ ...
        (valid_count(has_valid_data) .* energy_sum(has_valid_data) + eps);
    cf = max(0, min(1, cf));
    img = img_sum .* cf.^cf_alpha;
else
    img = img_sum;
end
ccf_dmas_time = toc;
if use_cf_das
    fprintf('\nCF-DAS 成像完成! 耗时 %.3f 秒\n', ccf_dmas_time);
else
    fprintf('\nDAS 成像完成! 耗时 %.3f 秒\n', ccf_dmas_time);
end

% -------------------------- 6. 包络检波+平滑+dB显示 --------------------------
img_env = abs(hilbert(img));

% 高斯平滑标准差（优先读取环境变量）
env_sigma = get_param_text(params, 'gaussian_sigma_mm', 'MATLAB_GAUSSIAN_SIGMA_MM');
if ~isempty(env_sigma)
    sigma = str2double(env_sigma);
else
    sigma = 0.4;
end

pixel_step = pixel_step_mm;
win_size = 2*ceil(3*sigma/pixel_step)+1;
h = fspecial('gaussian', [win_size win_size], sigma/pixel_step);
img_env_smooth = imfilter(img_env, h, 'replicate');

% 动态范围（优先读取环境变量）
env_dyn = get_param_text(params, 'dynamic_range_db', 'MATLAB_DYNAMIC_RANGE_DB');
if ~isempty(env_dyn)
    dynamic_range_db = str2double(env_dyn);
else
    dynamic_range_db = 20;
end

img_db = 20*log10(img_env_smooth / max(img_env_smooth(:)));
img_db(img_db < -dynamic_range_db) = -dynamic_range_db;

% 只对显示图插值，不改变保存到 MAT 的 img、img_db、x_img、z_img。
display_scale = get_param_number(params, 'display_scale', ...
    'MATLAB_DISPLAY_SCALE', 5);
if display_scale < 1
    error('DAS:InvalidDisplayScale', '显示插值倍数必须大于或等于1。');
end
if display_scale > 1
    img_db_display = imresize(img_db, display_scale, 'bicubic');
    img_db_display = max(-dynamic_range_db, min(0, img_db_display));
    x_display = linspace(x_img(1), x_img(end), size(img_db_display, 2));
    z_display = linspace(z_img(1), z_img(end), size(img_db_display, 1));
else
    img_db_display = img_db;
    x_display = x_img;
    z_display = z_img;
end

% -------------------------- 7. 绘图：B扫成像图 --------------------------
fig = figure('Color','w', 'Position', [100, 100, 900, 700]);

imagesc(x_display, z_display, img_db_display);
colormap jet;
colorbar;
caxis([-dynamic_range_db, 0]);
xlabel('横向位置 x (mm)', 'FontSize', 12);
ylabel('深度 z (mm)', 'FontSize', 12);
if use_cf_das
    title(sprintf('PAUT B扫成像 (CF-DAS, alpha = %.2f)', cf_alpha), 'FontSize', 14);
else
    title('PAUT B扫成像 (DAS)', 'FontSize', 14);
end
axis image;
set(gca, 'YDir','reverse', 'FontSize', 11);
grid off;
hold on;

% -------------------------- 8. 保存结果 --------------------------
if ~exist(save_folder, 'dir')
    mkdir(save_folder);
end

timestamp = datestr(now, 'yyyy-mm-dd_HH-MM-SS');
image_path = fullfile(save_folder, ['B扫成像_', timestamp, '.png']);
mat_path = fullfile(save_folder, ['B扫数据_', timestamp, '.mat']);
physical_image_path = fullfile(save_folder, 'das_physical.png');
saveas(fig, image_path);
raw_norm = (img_db - min(img_db(:))) / max(max(img_db(:)) - min(img_db(:)), eps);
imwrite(uint8(round(raw_norm * 255)), jet(256), physical_image_path);
save(mat_path, 'img', 'img_db', 'x_img', 'z_img');
close(fig);

result = struct('image_path', image_path, 'mat_path', mat_path, ...
    'physical_image_path', physical_image_path, ...
    'elapsed_seconds', ccf_dmas_time, 'cache_hit', cache_hit, ...
    'cache_file', cache_file, 'cache_key', cache_key, ...
    'use_cf_das', use_cf_das, 'cf_alpha', cf_alpha, ...
    'display_scale', display_scale);

fprintf('结果已保存到：%s\n', save_folder);
fprintf('==================== DAS 完成 ====================\n');
end


function digest = md5_text(text_value)
message_digest = java.security.MessageDigest.getInstance('MD5');
message_digest.update(uint8(unicode2native(char(text_value), 'UTF-8')));
hash_bytes = typecast(message_digest.digest(), 'uint8');
digest = lower(reshape(dec2hex(hash_bytes, 2).', 1, []));
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


function value = get_param_number(params, field_name, env_name, default_value)
text_value = get_param_text(params, field_name, env_name);
if isempty(text_value)
    value = default_value;
else
    value = str2double(text_value);
    if ~isfinite(value)
        error('DAS:InvalidNumericParam', '参数 %s 不是有效数字。', field_name);
    end
end
end


function value = get_param_bool(params, field_name, env_name, default_value)
text_value = lower(strtrim(get_param_text(params, field_name, env_name)));
if isempty(text_value)
    value = default_value;
elseif any(strcmp(text_value, {'1', 'true', 'yes', 'on'}))
    value = true;
elseif any(strcmp(text_value, {'0', 'false', 'no', 'off'}))
    value = false;
else
    error('DAS:InvalidBooleanParam', '参数 %s 不是有效布尔值。', field_name);
end
end
