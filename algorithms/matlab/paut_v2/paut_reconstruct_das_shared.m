function [envelope,img_db,x_img,z_img] = paut_reconstruct_das_shared( ...
        data_cube,time_axis,tx_positions,rx_positions,cfg)
%PAUT_RECONSTRUCT_DAS_SHARED Canonical DAS imaging used by PAUT workflows.
% The main pipeline and calibration programs call this same implementation
% so filtering, gating, delay interpolation, envelope and smoothing cannot
% silently diverge.

required={'bandpass_hz','filter_order','gate_start_us','gate_end_us', ...
    'gate_transition_us','x_img','z_img','velocity_mps','tx_z_mm', ...
    'rx_z_mm','gaussian_sigma_mm','db_floor'};
if ~all(isfield(cfg,required))
    missing=required(~isfield(cfg,required));
    error('DAS configuration is missing: %s',strjoin(missing,', '));
end
if size(data_cube,3)~=numel(tx_positions) || size(data_cube,2)~=numel(rx_positions)
    error('FMC cube dimensions do not match Tx/Rx position vectors.');
end

dt=median(diff(time_axis));
fs=1/dt;
n_sample=size(data_cube,1);
if cfg.bandpass_hz(2)>=fs/2
    error('采样率不足以支持当前带通上限。');
end
[b,a]=butter(cfg.filter_order,cfg.bandpass_hz/(fs/2),'bandpass');
t_us=(time_axis(:)-time_axis(1))*1e6;
gate=cosine_gate_shared(t_us,cfg.gate_start_us,cfg.gate_end_us,cfg.gate_transition_us);
for tx=1:size(data_cube,3)
    signals=data_cube(:,:,tx);
    signals=signals-mean(signals,1);
    signals=filtfilt(b,a,signals);
    data_cube(:,:,tx)=bsxfun(@times,signals,gate);
end

x_img=cfg.x_img;
z_img=cfg.z_img;
[X,Z]=meshgrid(x_img,z_img);
image_rf=zeros(size(X));
velocity_mmps=cfg.velocity_mps*1000;
for tx=1:numel(tx_positions)
    d_tx=hypot(X-tx_positions(tx),Z-cfg.tx_z_mm);
    for rx=1:numel(rx_positions)
        d_rx=hypot(X-rx_positions(rx),Z-cfg.rx_z_mm);
        sample_position=(d_tx+d_rx)/velocity_mmps/dt+1;
        idx0=floor(sample_position);
        fraction=sample_position-idx0;
        valid=idx0>=1 & idx0<n_sample;
        delayed=zeros(size(image_rf));
        signal=data_cube(:,rx,tx);
        indices=idx0(valid);
        delayed(valid)=(1-fraction(valid)).*signal(indices)+ ...
            fraction(valid).*signal(indices+1);
        image_rf=image_rf+delayed;
    end
end

envelope=abs(hilbert(image_rf));
sigma_pixels=cfg.gaussian_sigma_mm/median(diff(x_img));
window_size=2*ceil(3*sigma_pixels)+1;
kernel=fspecial('gaussian',[window_size window_size],sigma_pixels);
envelope=imfilter(envelope,kernel,'replicate');
normalization_peak=max(max(envelope(:)),realmin);
img_db=20*log10(envelope./normalization_peak+eps);
img_db(img_db<cfg.db_floor)=cfg.db_floor;
end


function gate=cosine_gate_shared(t_us,start_us,end_us,transition_us)
gate=zeros(size(t_us));
gate(t_us>=start_us & t_us<=end_us)=1;
rise=t_us>=start_us-transition_us & t_us<start_us;
gate(rise)=0.5*(1-cos(pi*(t_us(rise)-(start_us-transition_us))/transition_us));
fall=t_us>end_us & t_us<=end_us+transition_us;
gate(fall)=0.5*(1+cos(pi*(t_us(fall)-end_us)/transition_us));
end
