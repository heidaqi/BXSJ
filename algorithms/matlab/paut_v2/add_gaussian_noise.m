function [noisy_data,noise_info] = add_gaussian_noise(data_cube,snr_db,seed)
%ADD_GAUSSIAN_NOISE 按指定输入信噪比向FMC原始波形添加高斯白噪声。
%
%   noisy_data = ADD_GAUSSIAN_NOISE(data_cube,snr_db)
%   noisy_data = ADD_GAUSSIAN_NOISE(data_cube,snr_db,seed)
%   [noisy_data,noise_info] = ADD_GAUSSIAN_NOISE(...)
%
% 输入：
%   data_cube  - FMC波形，通常为[采样点, Rx, Tx]的single或double数组。
%   snr_db     - 目标输入信噪比(dB)。设为Inf时不添加噪声。
%   seed       - 可选随机种子，默认0，用于保证结果可重复。
%
% 信噪比定义：
%   先沿采样维去除各Tx-Rx通道的直流分量，再以全部通道的总体RMS
%   作为信号幅值。噪声标准差为 signal_rms/10^(snr_db/20)。
%
% 输出noise_info字段：
%   requested_snr_db - 指定信噪比
%   achieved_snr_db  - 本次随机噪声实际得到的信噪比
%   signal_rms       - 去直流后信号RMS
%   noise_sigma      - 高斯噪声的理论标准差
%   noise_rms        - 本次生成噪声的实际RMS
%   seed             - 使用的随机种子

if nargin < 3 || isempty(seed)
    seed = 0;
end

validateattributes(data_cube,{'single','double'}, ...
    {'real','nonempty'},mfilename,'data_cube',1);
validateattributes(snr_db,{'numeric'}, ...
    {'real','scalar','nonnan','positive'},mfilename,'snr_db',2);
validateattributes(seed,{'numeric'}, ...
    {'real','scalar','finite','integer','nonnegative','<=',2^32-1}, ...
    mfilename,'seed',3);

if any(~isfinite(data_cube(:)))
    error('add_gaussian_noise:NonfiniteData', ...
        'data_cube包含NaN或Inf，不能可靠计算信噪比。');
end

centered_data = data_cube-mean(data_cube,1);
signal_rms = sqrt(mean(double(centered_data(:)).^2));
if signal_rms <= 0
    error('add_gaussian_noise:ZeroSignal', ...
        'data_cube去直流后的RMS为0，无法按信噪比添加噪声。');
end

if isinf(snr_db)
    noisy_data = data_cube;
    noise_sigma = 0;
    noise_rms = 0;
    achieved_snr_db = Inf;
else
    noise_sigma = signal_rms/10^(double(snr_db)/20);

    % 仅在函数内部使用指定随机种子，不改变调用者的全局随机数状态。
    previous_rng = rng;
    rng_cleanup = onCleanup(@() rng(previous_rng)); %#ok<NASGU>
    rng(double(seed),'twister');
    noise = cast(noise_sigma,'like',data_cube).* ...
        randn(size(data_cube),'like',data_cube);

    noisy_data = data_cube+noise;
    noise_rms = sqrt(mean(double(noise(:)).^2));
    achieved_snr_db = 20*log10(signal_rms/noise_rms);
end

noise_info = struct( ...
    'requested_snr_db',double(snr_db), ...
    'achieved_snr_db',double(achieved_snr_db), ...
    'signal_rms',double(signal_rms), ...
    'noise_sigma',double(noise_sigma), ...
    'noise_rms',double(noise_rms), ...
    'seed',double(seed));
end
