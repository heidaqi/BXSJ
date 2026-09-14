function paut = load_data(data_folder, tx_positions, rx_positions)
% ============================================================
% loadPAUTData
% PAUT原始数据读取函数
%
% 输入：
%   data_folder    - 数据文件夹
%   tx_positions   - Tx位置，例如 [30 32 ... 63]
%   rx_positions   - Rx位置，例如 [30 32 ... 63]
%
% 输出：
%   paut           - PAUT数据结构体
%
% 自动识别并读取三种数据格式：
%   1) 根目录按 Tx 位置命名的多 txt 文件（第 2 列为时间，后续列为 Rx）；
%   2) tx_1..tx_N/001..NNN.txt 目录格式（每个文件为 [time, pressure]）；
%   3) 单个 FMC txt（第 1 列时间，后续列按 Tx 外层、Rx 内层排列）。
% ============================================================

    n_tx = length(tx_positions);
    n_rx = length(rx_positions);

    % 单个 FMC 文件可以直接作为数据源传入。
    if isfile(data_folder)
        paut = load_fmc_single_format(data_folder, n_tx, n_rx);
        paut = finalize_paut(paut, data_folder, tx_positions, rx_positions);
        fprintf('数据格式：单文件 FMC，Tx=%d，Rx=%d，采样点=%d\n', n_tx, n_rx, paut.n_sample);
        return;
    end

    format_name = detect_format(data_folder, n_tx, n_rx);
    if strcmp(format_name, 'dir')
        paut = load_dir_format(data_folder, n_tx, n_rx);
        paut = finalize_paut(paut, data_folder, tx_positions, rx_positions);
        fprintf('数据格式：目录格式，Tx=%d，Rx=%d，采样点=%d\n', n_tx, n_rx, paut.n_sample);
        return;
    elseif strcmp(format_name, 'fmc_single')
        paut = load_fmc_single_format(data_folder, n_tx, n_rx);
        paut = finalize_paut(paut, data_folder, tx_positions, rx_positions);
        fprintf('数据格式：单文件 FMC，Tx=%d，Rx=%d，采样点=%d\n', n_tx, n_rx, paut.n_sample);
        return;
    end

    % --------------------------------------------------------
    % 1. 查找txt文件
    % --------------------------------------------------------

    file_list = dir(fullfile(data_folder, '*.txt'));

    if isempty(file_list)
        error('数据文件夹中没有找到txt文件：%s', data_folder);
    end

    fprintf('数据文件夹：%s\n', data_folder);
    fprintf('找到 %d 个txt文件\n', length(file_list));

    % --------------------------------------------------------
    % 2. 根据文件名中的数字排序；跳过文件名无法解析为数字的文件
    %    （如 readme.txt、说明.txt 等非数据文件）
    % --------------------------------------------------------

    keep_idx = false(length(file_list), 1);
    file_num = zeros(length(file_list), 1);

    for i = 1:length(file_list)

        [~, name_only, ~] = fileparts(file_list(i).name);

        num_val = str2double(name_only);

        % 注意：str2double('inf') 返回 Inf 而非 NaN，所以必须用 isfinite
        % 同时过滤 NaN / Inf / -Inf（如 readme.txt、inf.txt 等）
        if ~isfinite(num_val)
            fprintf('跳过非数据文件（文件名无法解析为数字）：%s\n', ...
                file_list(i).name);
        else
            keep_idx(i) = true;
            file_num(i) = num_val;
        end

    end

    file_list = file_list(keep_idx);
    file_num = file_num(keep_idx);

    if isempty(file_list)
        error('数据文件夹中没有可用的数字命名txt文件：%s', data_folder);
    end

    fprintf('有效数据文件：%d 个（已跳过 %d 个非数据文件）\n', ...
        length(file_list), sum(~keep_idx));

    [~, sort_idx] = sort(file_num);

    file_list = file_list(sort_idx);
    file_num = file_num(sort_idx);

    % --------------------------------------------------------
    % 3. 检查Tx数量
    % --------------------------------------------------------

    if length(file_list) ~= n_tx

        error(['文件数量(%d)与Tx数量(%d)不一致。\n' ...
               '请检查数据文件或tx_positions。'], ...
               length(file_list), n_tx);

    end

    % --------------------------------------------------------
    % 4. 检查文件名与Tx位置
    % --------------------------------------------------------

    if any(file_num(:)' ~= tx_positions(:)')

        warning('文件名位置与tx_positions不完全一致。');

        fprintf('文件位置：');
        fprintf(' %.1f', file_num);
        fprintf('\n');

        fprintf('设定Tx：');
        fprintf(' %.1f', tx_positions);
        fprintf('\n');

    end

    % --------------------------------------------------------
    % 5. 读取第一个文件
    % --------------------------------------------------------

    first_file = fullfile(data_folder, file_list(1).name);

    tmp = read_matrix_txt(first_file);

    expected_cols = 2 + n_rx;

    if size(tmp,2) < expected_cols
        error(['文件 %s 列数不足。\n' ...
               '当前列数：%d，需要至少：%d'], ...
               file_list(1).name, ...
               size(tmp,2), ...
               expected_cols);
    end

    time_axis = tmp(:,2);

    n_sample = length(time_axis);

    % --------------------------------------------------------
    % 6. 计算采样参数
    % --------------------------------------------------------

    dt = mean(diff(time_axis));
    fs = 1 / dt;

    fprintf('采样点数：%d\n', n_sample);
    fprintf('dt = %.3f ns\n', dt*1e9);
    fprintf('fs = %.3f MHz\n', fs/1e6);

    % --------------------------------------------------------
    % 7. 创建三维数据矩阵
    %
    % data(sample, tx, rx)
    % --------------------------------------------------------

    data = zeros(n_sample, n_tx, n_rx);

    % --------------------------------------------------------
    % 8. 读取所有Tx文件
    % --------------------------------------------------------

    for tx_idx = 1:n_tx

        file_path = fullfile( ...
            data_folder, ...
            file_list(tx_idx).name);

        fprintf('读取 Tx %d/%d：%s\n', ...
            tx_idx, ...
            n_tx, ...
            file_list(tx_idx).name);

        tmp = read_matrix_txt(file_path);

        % 检查采样点数
        if size(tmp,1) ~= n_sample

            error(['文件 %s 采样点数不一致。\n' ...
                   '期望：%d，实际：%d'], ...
                   file_list(tx_idx).name, ...
                   n_sample, ...
                   size(tmp,1));

        end

        % 检查时间轴
        current_time = tmp(:,2);

        if any(abs(current_time - time_axis) > 1e-12)

            warning('文件 %s 的时间轴与第一个文件不完全一致。', ...
                file_list(tx_idx).name);

        end

        % ----------------------------------------------------
        % 提取16个Rx信号
        %
        % 第3~18列
        % ----------------------------------------------------

        data(:,tx_idx,:) = tmp(:,3:2+n_rx);

    end

    % --------------------------------------------------------
    % 9. 保存到结构体
    % --------------------------------------------------------

    paut.data = data;

    paut.time = time_axis;

    paut.tx_positions = tx_positions;

    paut.rx_positions = rx_positions;

    paut.n_tx = n_tx;

    paut.n_rx = n_rx;

    paut.n_sample = n_sample;

    paut.dt = dt;

    paut.fs = fs;

    paut.files = {file_list.name};

    paut.data_folder = data_folder;

end

function paut = finalize_paut(paut, data_folder, tx_positions, rx_positions)
    paut.tx_positions = tx_positions;
    paut.rx_positions = rx_positions;
    paut.n_tx = length(tx_positions);
    paut.n_rx = length(rx_positions);
    paut.n_sample = size(paut.data, 1);
    paut.dt = mean(diff(paut.time));
    paut.fs = 1 / paut.dt;
    paut.data_folder = data_folder;
end

function format_name = detect_format(data_folder, n_tx, n_rx)
    if isfile(data_folder)
        matrix = read_matrix_txt(data_folder);
        if size(matrix, 2) < 1 + n_tx * n_rx
            error('FMC 文件列数不足：%d < %d', size(matrix, 2), 1 + n_tx * n_rx);
        end
        format_name = 'fmc_single';
        return;
    end
    tx_dirs = dir(fullfile(data_folder, 'tx_*'));
    if any([tx_dirs.isdir])
        format_name = 'dir';
        return;
    end
    files = dir(fullfile(data_folder, '*.txt'));
    files = files(~[files.isdir]);
    if isempty(files)
        error('数据目录中没有 txt 文件：%s', data_folder);
    end
    first = read_matrix_txt(fullfile(data_folder, files(1).name));
    if size(first, 2) >= 1 + n_tx * n_rx
        format_name = 'fmc_single';
    else
        format_name = 'legacy';
    end
end

function paut = load_dir_format(data_folder, n_tx, n_rx)
    tx_dirs = sort_dirs_by_number(dir(fullfile(data_folder, 'tx_*')));
    if numel(tx_dirs) ~= n_tx
        error('tx_* 子目录数量(%d)与 Tx 数量(%d)不一致。', numel(tx_dirs), n_tx);
    end
    first_dir = fullfile(data_folder, tx_dirs(1).name);
    rx_files = sort_rx_files(first_dir);
    if numel(rx_files) ~= n_rx
        error('目录 %s 内 txt 数量(%d)与 Rx 数量(%d)不一致。', tx_dirs(1).name, numel(rx_files), n_rx);
    end
    first = read_matrix_txt(fullfile(first_dir, rx_files(1).name));
    if size(first, 2) < 2
        error('文件 %s 列数不足（需 [time, pressure] 两列）。', rx_files(1).name);
    end
    paut.time = first(:, 1);
    paut.data = zeros(numel(paut.time), n_tx, n_rx);
    for tx_idx = 1:n_tx
        rx_files = sort_rx_files(fullfile(data_folder, tx_dirs(tx_idx).name));
        if numel(rx_files) ~= n_rx
            error('目录 %s 内 txt 数量(%d)与 Rx 数量(%d)不一致。', tx_dirs(tx_idx).name, numel(rx_files), n_rx);
        end
        for rx_idx = 1:n_rx
            current = read_matrix_txt(fullfile(data_folder, tx_dirs(tx_idx).name, rx_files(rx_idx).name));
            if size(current, 1) ~= numel(paut.time) || size(current, 2) < 2
                error('文件 %s 的数据尺寸不正确。', rx_files(rx_idx).name);
            end
            paut.data(:, tx_idx, rx_idx) = current(:, 2);
        end
    end
    paut.files = {tx_dirs.name};
end

function paut = load_fmc_single_format(data_folder, n_tx, n_rx)
    if isfile(data_folder)
        file_path = data_folder;
        [~, file_name, extension] = fileparts(file_path);
        display_name = [file_name extension];
    else
        files = dir(fullfile(data_folder, '*.txt'));
        files = files(~[files.isdir]);
        if isempty(files)
            error('数据目录中没有 FMC txt 文件：%s', data_folder);
        end
        file_path = fullfile(data_folder, files(1).name);
        display_name = files(1).name;
    end
    matrix = read_matrix_txt(file_path);
    required_columns = 1 + n_tx * n_rx;
    if size(matrix, 2) < required_columns
        error('FMC 文件列数不足：%d < %d', size(matrix, 2), required_columns);
    end
    paut.time = matrix(:, 1);
    paut.data = reshape(matrix(:, 2:required_columns), size(matrix, 1), n_rx, n_tx);
    paut.data = permute(paut.data, [1, 3, 2]);
    paut.files = {display_name};
end

function matrix = read_matrix_txt(file_path)
    try
        matrix = readmatrix(file_path, 'FileType', 'text', 'CommentStyle', '%', ...
            'Delimiter', {' ', sprintf('\t'), ','}, 'ConsecutiveDelimitersRule', 'join');
    catch
        matrix = load(file_path);
    end
    if isempty(matrix)
        error('无法解析数据文件：%s', file_path);
    end
    matrix = matrix(~all(isnan(matrix), 2), :);
end

function sorted_dirs = sort_dirs_by_number(dirs)
    dirs = dirs([dirs.isdir]);
    numbers = nan(numel(dirs), 1);
    for index = 1:numel(dirs)
        token = regexp(dirs(index).name, '(\d+)\s*$', 'tokens', 'once');
        if ~isempty(token)
            numbers(index) = str2double(token{1});
        end
    end
    [numbers, order] = sort(numbers);
    sorted_dirs = dirs(order);
    sorted_dirs = sorted_dirs(isfinite(numbers));
end

function rx_files = sort_rx_files(tx_dir)
    files = dir(fullfile(tx_dir, '*.txt'));
    files = files(~[files.isdir]);
    numbers = nan(numel(files), 1);
    for index = 1:numel(files)
        [~, name, ~] = fileparts(files(index).name);
        numbers(index) = str2double(name);
    end
    [numbers, order] = sort(numbers);
    files = files(order);
    rx_files = files(isfinite(numbers));
end
