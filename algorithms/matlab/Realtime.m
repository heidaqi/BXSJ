% ============================================================
% 组合实时处理脚本
%
% 目的：把 DAS / Ascan / TOFD 三个流程合并到「一次」MATLAB 进程里运行，
%       避免 backend 对每个数据组三次调用 matlab.exe -batch 造成的
%       三次冷启动（每次约 15~30 秒），从而显著缩短每组处理间隔。
%
% 顺序：
%   1. DAS 成像（必须成功，生成 B扫成像_*.png）
%   2. Ascan 检测（失败不阻断，生成 ascan_results.csv）
%   3. TOFD 成像（失败不阻断，生成 tofd_bscan.png）
%
% DAS / Ascan 是函数，不再 clear 常驻 Engine 工作区；路径和参数仍兼容环境变量。
% ============================================================

% 禁止显示图形窗口（后台批量处理，DAS.m 已设置一次，这里再兜底）
set(0, 'DefaultFigureVisible', 'off');

% 当前脚本所在目录（= algorithms/matlab），确保子脚本能被 run 找到
this_dir = fileparts(mfilename('fullpath'));
if ~isempty(this_dir)
    cd(this_dir);
end

fprintf('\n============================================\n');
fprintf('组合实时处理开始（单进程：DAS + Ascan + TOFD）\n');
fprintf('============================================\n');

% -------------------------- 1. DAS 成像 --------------------------
fprintf('\n---------- [1/3] DAS 成像 ----------\n');
DAS();

% -------------------------- 2. Ascan 检测 --------------------------
fprintf('\n---------- [2/3] Ascan 检测 ----------\n');
try
    Ascan();
catch ME
    fprintf('Ascan 检测失败（DAS 成像仍可展示）: %s\n', ME.message);
end

% -------------------------- 3. TOFD 成像 --------------------------
tofd_enabled = getenv('MATLAB_TOFD_ENABLED');
tofd_on = isempty(tofd_enabled) || ...
          strcmpi(tofd_enabled, 'true') || strcmpi(tofd_enabled, '1');
if tofd_on
    fprintf('\n---------- [3/3] TOFD 成像 ----------\n');
    try
        run(fullfile(this_dir, 'TOFD.m'));
    catch ME
        fprintf('TOFD 成像失败（DAS 成像仍可展示）: %s\n', ME.message);
    end
else
    fprintf('\n---------- [3/3] TOFD 成像（已禁用） ----------\n');
end

fprintf('\n============================================\n');
fprintf('组合实时处理完成\n');
fprintf('============================================\n');
