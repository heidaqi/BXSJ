clear; clc; close all;
% 禁止显示图形窗口（后台批量处理）
set(0, 'DefaultFigureVisible', 'off');

% ============================================================
% TOFD 成像（一发一收 B 扫 + 当前批次全量累计）
%
% 原理：
%   - 每个数据组（文件夹）= 一道 B 扫
%   - 默认发射 Tx=22、接收 Rx=70（一发一收）
%   - x 轴为当前处理批次的数据组序号，不限制道数
%   - y 轴为时间（μs）
%
% 状态（累计矩阵）保存在 MATLAB_TOFD_STATE_DIR/tofd_state.mat，
% 每次调用把当前组 A 扫追加为新的第 N 道；新批次由后端清空状态。
% ============================================================

% -------------------------- 0. 路径 --------------------------
env_data_folder = getenv('MATLAB_DATA_DIR');
if ~isempty(env_data_folder)
    data_folder = env_data_folder;
else
    data_folder = '.\data\groups';
end
data_folder = regexprep(data_folder, '[\\/]+$', '');

env_save_folder = getenv('MATLAB_OUTPUT_DIR');
if ~isempty(env_save_folder)
    save_folder = env_save_folder;
else
    save_folder = '.\res';
end

env_state_dir = getenv('MATLAB_TOFD_STATE_DIR');
if ~isempty(env_state_dir)
    state_dir = env_state_dir;
else
    state_dir = fullfile(save_folder, 'tofd_state');
end
state_dir = regexprep(state_dir, '[\\/]+$', '');

% -------------------------- 1. 参数 --------------------------
tx_positions = [30 32 34 36 38 40 42 44 49 51 53 55 57 59 61 63];
rx_positions = tx_positions;

% 一发一收（默认 30 发射、63 接收）
tx_pick = 30;
rx_pick = 63;
env_tx = getenv('MATLAB_TOFD_TX');
if ~isempty(env_tx), tx_pick = str2double(env_tx); end
env_rx = getenv('MATLAB_TOFD_RX');
if ~isempty(env_rx), rx_pick = str2double(env_rx); end

% 兼容旧调用参数，但当前批次不再按窗口截断。
window_size = 0;

% 材料参数
env_velocity = getenv('MATLAB_VELOCITY_MPS');
if ~isempty(env_velocity)
    velocity_mps = str2double(env_velocity);
else
    velocity_mps = 5900;
end
velocity_mmpers = velocity_mps * 1000;

env_thickness = getenv('MATLAB_PLATE_THICKNESS_MM');
if ~isempty(env_thickness)
    plate_thickness = str2double(env_thickness);
else
    plate_thickness = 40;
end

fprintf('\n============================================\n');
fprintf('TOFD 成像: Tx=%d -> Rx=%d, 当前批次全量累计（不限道数）\n', tx_pick, rx_pick);
fprintf('数据目录：%s\n', data_folder);
fprintf('============================================\n');

% -------------------------- 2. 读取数据 --------------------------
paut = load_data(data_folder, tx_positions, rx_positions);
data = paut.data;
time_axis = paut.time;
n_sample = paut.n_sample;

% -------------------------- 2.5 统一时间长度（截断） --------------------------
% 不同数据组的时间轴长度可能不一致（例如 12μs≈598点 与 15μs≈748点），
% 直接水平拼接累计矩阵会报「要串联的数组的维度不一致」。
% 统一截断到 max_time_us（默认 12 μs），超出的采样点丢弃，保证各组点数一致。
env_max_time = getenv('MATLAB_TOFD_MAX_TIME_US');
max_time_us = 12;
if ~isempty(env_max_time)
    max_time_us = str2double(env_max_time);
end
if ~isfinite(max_time_us) || max_time_us <= 0
    max_time_us = 12;
end
keep = time_axis <= (max_time_us * 1e-6);
if any(keep)
    data = data(keep, :, :);
    time_axis = time_axis(keep);
    n_sample = length(time_axis);
    fprintf('TOFD 时间轴统一：截断到 %.3f μs，共 %d 点\n', max_time_us, n_sample);
end

[~, tx_idx] = min(abs(tx_positions - tx_pick));
[~, rx_idx] = min(abs(rx_positions - rx_pick));

ascan = data(:, tx_idx, rx_idx);
ascan = ascan - mean(ascan);   % 去直流

% -------------------------- 3. 更新累计矩阵 --------------------------
state_file = fullfile(state_dir, 'tofd_state.mat');
if exist(state_file, 'file')
    S = load(state_file);
    tofd_matrix = S.tofd_matrix;
    group_labels = S.group_labels;
else
    tofd_matrix = zeros(n_sample, 0);
    group_labels = {};
end

% 对齐累计矩阵与当前 A 扫的采样点数（取两者最小值），防止点数不一致导致拼接失败
n_common = min(size(tofd_matrix, 1), size(ascan, 1));
tofd_matrix = tofd_matrix(1:n_common, :);
ascan = ascan(1:n_common);
time_axis = time_axis(1:n_common);
n_sample = n_common;

% 追加当前组作为新的一道
tofd_matrix = [tofd_matrix, ascan];           % n_sample x n_ch
[~, group_name, ~] = fileparts(data_folder);
if isempty(group_name), group_name = data_folder; end
group_labels{end+1} = group_name;

n_ch = size(tofd_matrix, 2);

% -------------------------- 4. 保存状态 --------------------------
if ~exist(state_dir, 'dir'), mkdir(state_dir); end
save(state_file, 'tofd_matrix', 'group_labels', 'time_axis', 'n_sample');

% -------------------------- 5. 成像渲染 --------------------------
env_dB = abs(hilbert(tofd_matrix));
env_norm = env_dB / max(env_dB(:));
env_dB = 20 * log10(env_norm + 1e-10);
env_dB(env_dB < -25) = -25;

pos_axis = 1:n_ch;
time_us = time_axis * 1e6;

figure('Color', 'w', 'Position', [100, 100, 1000, 420]);
imagesc(pos_axis, time_us, env_dB);
colormap(flipud(gray));
colorbar;
xlabel('数据组序号（道）', 'FontSize', 12);
ylabel('时间 (\mus)', 'FontSize', 12);
title(sprintf('TOFD B扫成像 (Tx=%d \\rightarrow Rx=%d, 当前批次 %d 道)', tx_pick, rx_pick, n_ch), 'FontSize', 13);
set(gca, 'YDir', 'reverse', 'FontSize', 11);

% -------------------------- 6. 保存结果 --------------------------
if ~exist(save_folder, 'dir'), mkdir(save_folder); end
saveas(gcf, fullfile(save_folder, 'tofd_bscan.png'));
save(fullfile(save_folder, 'tofd_bscan.mat'), 'tofd_matrix', 'group_labels', 'time_axis', 'n_ch');

% 写一个 info 文件，方便后端读取当前道数
fid = fopen(fullfile(save_folder, 'tofd_info.txt'), 'w');
fprintf(fid, 'channel_count=%d\nwindow_size=%d\ntx=%d\nrx=%d\n', n_ch, window_size, tx_pick, rx_pick);
fclose(fid);

fprintf('TOFD 成像完成：当前批次累计 %d 道（不限道数）\n', n_ch);
