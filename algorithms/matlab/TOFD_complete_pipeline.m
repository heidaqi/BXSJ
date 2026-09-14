function result = TOFD_complete_pipeline(data_root, output_dir, params_json)
%TOFD_COMPLETE_PIPELINE Headless TOFD entry for compact v6 MAT or legacy binary.
% The compact path reads validated Tx1->Rx128 int16 traces directly. The
% signal-processing and imaging stages remain identical to the v5 workflow.
% Verified binary layout per frame:
%   128-byte header, then sample-interleaved int16 data:
%   [sample1 ch1..ch128], [sample2 ch1..ch128], ...
% Each sample file may end with zero padding after its complete frames.
% Recorded sample 1 is treated as transmit time zero. No pulse alignment.

if nargin < 1 || isempty(data_root), error('TOFD data root is required.'); end
if nargin < 2 || isempty(output_dir), error('TOFD output directory is required.'); end
if nargin < 3 || isempty(params_json), params_json = '{}'; end
params = jsondecode(char(params_json));
if ~isfolder(output_dir), mkdir(output_dir); end
old_visibility = get(0,'DefaultFigureVisible');
set(0,'DefaultFigureVisible','off');
cleanup_figures = onCleanup(@() restore_figures(old_visibility)); %#ok<NASGU>

%% User settings
tx_no = number_param(params,'tofd_tx_channel',1);
rx_no = number_param(params,'tofd_rx_channel',128);
header_bytes = number_param(params,'tofd_header_bytes',128);
sample_type = 'int16=>double';
bytes_per_sample = 2;
data_layout = 'sample-interleaved';

pcs = number_param(params,'tofd_pcs_mm',42)*1e-3;
c = number_param(params,'velocity_mps',5900);
thickness = number_param(params,'plate_thickness_mm',6.3)*1e-3;
filter_band = [1.5e6 4.5e6];  % Hz
db_range = [-30 0];
time_margin_us = 1.5;
arrival_fraction = 0.10;      % relative onset threshold
arrival_noise_sigma = 6;      % robust noise threshold
arrival_hold_us = 0.08;       % threshold must persist this long
arrival_search_us = [0.5 50]; % broad recorded-time search; no time correction

data_root = char(data_root);
compact_mode = false;
compact_data_file = '';
source_format = 'legacy_binary';
if isfile(data_root)
    [~,~,input_ext] = fileparts(data_root);
    if ~strcmpi(input_ext,'.mat')
        error('TOFD input file must be a compact MAT file: %s',data_root);
    end
    compact_mode = true;
    compact_data_file = data_root;
elseif isfolder(data_root)
    compact_candidates = dir(fullfile(data_root,'tx1_rx128_raw*.mat'));
    if numel(compact_candidates) == 1
        compact_mode = true;
        compact_data_file = fullfile(compact_candidates(1).folder,compact_candidates(1).name);
    elseif numel(compact_candidates) > 1
        error('Multiple compact Tx1-Rx128 MAT files were found in %s',data_root);
    end
else
    error('TOFD input does not exist: %s',data_root);
end

if compact_mode
    %% Compact v6 MAT metadata and hard validation
    source_format = 'compact_mat_v6';
    required_vars = {'validation_passed','validation_mismatch_count', ...
        'tx_no','rx_no','nsamples','fs_hz','original_rx_channels', ...
        'total_frames','nscan','scan_names','scan_numbers', ...
        'scan_frame_start','scan_frame_count'};
    compact = load(compact_data_file,required_vars{:});
    for k = 1:numel(required_vars)
        assert(isfield(compact,required_vars{k}), ...
            'Required variable "%s" is missing from %s', ...
            required_vars{k},compact_data_file);
    end
    assert(logical(compact.validation_passed), ...
        'Compact MAT is not marked as successfully validated.');
    assert(double(compact.validation_mismatch_count) == 0, ...
        'Compact MAT reports write-verification mismatches.');
    assert(double(compact.tx_no) == tx_no && double(compact.rx_no) == rx_no, ...
        'Requested Tx%d-Rx%d does not match compact MAT Tx%d-Rx%d.', ...
        tx_no,rx_no,double(compact.tx_no),double(compact.rx_no));

    ns = double(compact.nsamples);
    fs = double(compact.fs_hz);
    n_rx_channels = double(compact.original_rx_channels);
    nscan = double(compact.nscan);
    total_frames = double(compact.total_frames);
    scan_names = compact.scan_names;
    scan_numbers = double(compact.scan_numbers(:));
    scan_frame_start = double(compact.scan_frame_start(:));
    scan_frame_count = double(compact.scan_frame_count(:));
    assert(isscalar(ns) && ns == fix(ns) && ns > 0,'Invalid sample count.');
    assert(isscalar(fs) && fs > 0,'Invalid sampling frequency.');
    assert(numel(scan_names) == nscan && numel(scan_numbers) == nscan, ...
        'Scan metadata size does not match nscan.');
    assert(numel(scan_frame_start) == nscan && numel(scan_frame_count) == nscan, ...
        'Frame mapping size does not match nscan.');
    assert(all(scan_frame_count > 0) && sum(scan_frame_count) == total_frames, ...
        'Compact MAT frame mapping is invalid.');

    raw_file = matfile(compact_data_file);
    raw_size = size(raw_file,'rx128');
    assert(isequal(raw_size,[ns total_frames]), ...
        'rx128 must be [%d x %d], but it is [%d x %d].', ...
        ns,total_frames,raw_size(1),raw_size(2));
    sample_probe = raw_file.rx128(1,1);
    assert(isa(sample_probe,'int16'),'rx128 must contain unscaled int16 samples.');

    scan_dirs = repmat(struct('name','','folder',''),nscan,1);
    for k = 1:nscan
        scan_dirs(k).name = scan_names{k};
        scan_dirs(k).folder = fileparts(compact_data_file);
    end
    frame_bytes = NaN;
else
%% Scan folders: sort by the final numeric scan index
scan_dirs = dir(fullfile(data_root, 'Save_*'));
scan_dirs = scan_dirs([scan_dirs.isdir]);
if isempty(scan_dirs)
    error('No Save_* folders found in: %s', data_root);
end
scan_numbers = nan(size(scan_dirs));
for k = 1:numel(scan_dirs)
    token = regexp(scan_dirs(k).name, '_(\d+)$', 'tokens', 'once');
    if ~isempty(token), scan_numbers(k) = str2double(token{1}); end
end
if all(isfinite(scan_numbers)) && numel(unique(scan_numbers)) == numel(scan_numbers)
    [~, order] = sort(scan_numbers);
else
    warning('Final numeric indices are missing/duplicated; using name order.');
    [~, order] = sort({scan_dirs.name});
end
scan_dirs = scan_dirs(order);
nscan = numel(scan_dirs);

%% Acquisition parameters and hard validation
param_file = fullfile(scan_dirs(1).folder, scan_dirs(1).name, 'Param.mat');
S = load(param_file);
assert(isfield(S, 'AcqConfigSegment'), 'AcqConfigSegment missing in %s', param_file);
param = S.AcqConfigSegment;
ns = double(param.ScanList.SampleN);
fs = double(param.Rx.fs);
n_rx_channels = double(param.Tx.channel);
assert(isscalar(ns) && ns == fix(ns) && ns > 0, 'Invalid SampleN.');
assert(isscalar(fs) && fs > 0, 'Invalid sampling frequency.');
assert(rx_no >= 1 && rx_no <= n_rx_channels, 'Rx channel is outside stored range.');
assert(filter_band(1) > 0 && filter_band(2) < fs/2, ...
       'Band-pass %.2f-%.2f MHz is invalid for fs=%.2f MHz.', ...
       filter_band(1)/1e6, filter_band(2)/1e6, fs/1e6);

frame_bytes = header_bytes + ns*n_rx_channels*bytes_per_sample;
total_frames = NaN;
scan_names = {scan_dirs.name}';
scan_frame_start = [];
scan_frame_count = [];
end

assert(filter_band(1) > 0 && filter_band(2) < fs/2, ...
       'Band-pass %.2f-%.2f MHz is invalid for fs=%.2f MHz.', ...
       filter_band(1)/1e6,filter_band(2)/1e6,fs/1e6);
dt = 1/fs;
time_s = (0:ns-1)'*dt;
time_us = time_s*1e6;
[bp_b, bp_a] = butter(4, filter_band/(fs/2), 'bandpass');

%% Read Rx128 and combine repeated frames robustly
single_frame_rf = nan(ns, nscan);
coherent_rf = nan(ns, nscan);
robust_envelope = nan(ns, nscan);
repeat_total = zeros(1, nscan);
repeat_used = zeros(1, nscan);

fprintf('Tx%d -> Rx%d; %d scan positions; fs %.3f MHz; %d samples\n', ...
        tx_no, rx_no, nscan, fs/1e6, ns);

for iscan = 1:nscan
    if compact_mode
        first_col = scan_frame_start(iscan);
        last_col = first_col + scan_frame_count(iscan) - 1;
        assert(first_col >= 1 && last_col <= total_frames, ...
            'Frame range is invalid at scan %d.',iscan);
        traces = double(raw_file.rx128(:,first_col:last_col));
    else
    scan_folder = fullfile(scan_dirs(iscan).folder, scan_dirs(iscan).name);
    bin_files = dir(fullfile(scan_folder, '*.bin'));
    [~, bo] = sort({bin_files.name});
    bin_files = bin_files(bo);
    traces = zeros(ns, 0);

    for ibin = 1:numel(bin_files)
        filename = fullfile(bin_files(ibin).folder, bin_files(ibin).name);
        info = dir(filename);
        % Actual files contain complete frames followed by a small zero pad
        % (384 bytes in the supplied sample). Never treat that pad as a frame.
        nframe = floor(double(info.bytes)/frame_bytes);
        trailing_bytes = double(info.bytes)-nframe*frame_bytes;
        % Do not assume Param.Rx.NumsPerFile or a fixed number of files.
        % The final bin may contain fewer frames than earlier bins.
        if nframe == 0
            warning('Skipping %s: it contains no complete frame.',filename);
            continue;
        end
        fprintf('    %s: %d complete frames',bin_files(ibin).name,nframe);
        if trailing_bytes >= frame_bytes
            error('Internal frame-count error in %s.',filename);
        elseif trailing_bytes > 0
            fprintf(', ignoring %d trailing padding bytes',trailing_bytes);
        end
        fprintf('\n');
        fid = fopen(filename, 'rb', 'ieee-le');
        if fid < 0, error('Cannot open: %s', filename); end
        cleanup = onCleanup(@() fclose(fid));

        if trailing_bytes > 0
            fseek(fid,nframe*frame_bytes,'bof');
            pad = fread(fid,trailing_bytes,'uint8=>uint8');
            if any(pad ~= 0)
                warning('%s has %d nonzero bytes after its complete frames.', ...
                        filename,nnz(pad));
            end
        end

        for iframe = 1:nframe
            frame_data_start = (iframe-1)*frame_bytes + header_bytes;
            switch data_layout
                case 'channel-major'
                    offset = frame_data_start + (rx_no-1)*ns*bytes_per_sample;
                    assert(fseek(fid, offset, 'bof') == 0, 'Seek failed in %s', filename);
                    x = fread(fid, ns, sample_type);
                case 'sample-interleaved'
                    offset = frame_data_start + (rx_no-1)*bytes_per_sample;
                    assert(fseek(fid, offset, 'bof') == 0, 'Seek failed in %s', filename);
                    x = fread(fid, ns, sample_type, (n_rx_channels-1)*bytes_per_sample);
                otherwise
                    error('Unknown data_layout: %s', data_layout);
            end
            if numel(x) == ns, traces(:,end+1) = x; end %#ok<SAGROW>
        end
        clear cleanup
    end

    if isempty(traces), error('No valid Rx%d frames in %s', rx_no, scan_folder); end
    end
    repeat_total(iscan) = size(traces,2);

    % Per-frame DC removal and zero-phase filtering.
    adc_saturated = any(traces == intmax('int16') | traces == intmin('int16'),1);
    traces = traces - median(traces,1);
    traces = filtfilt(bp_b, bp_a, traces);

    % Reject saturated/abnormally energetic frames without time-shifting data.
    energy = sqrt(mean(traces.^2,1));
    med_e = median(energy);
    mad_e = 1.4826*median(abs(energy-med_e));
    good = ~adc_saturated & isfinite(energy);
    if mad_e > 0, good = good & abs(energy-med_e) <= 6*mad_e; end
    if ~any(good)
        warning('All frames rejected at scan %d; retaining all finite frames.', iscan);
        good = isfinite(energy);
    end
    traces = traces(:,good);
    repeat_used(iscan) = size(traces,2);

    single_frame_rf(:,iscan) = traces(:,1);
    coherent_rf(:,iscan) = mean(traces,2);
    robust_envelope(:,iscan) = median(abs(hilbert(traces)),2);
    if mod(iscan,10)==0 || iscan==nscan
        fprintf('  %d/%d positions loaded\n', iscan, nscan);
    end
end

%% TOFD geometry: transmit time is exactly zero
t_lateral_s = pcs/c;
t_bottom_s = sqrt(pcs^2 + (2*thickness)^2)/c;
if time_s(end) < t_bottom_s
    error('Record ends at %.3f us, before expected back-wall time %.3f us.', ...
          time_us(end), t_bottom_s*1e6);
end

physical_gate = time_s >= max(0,t_lateral_s-time_margin_us*1e-6) & ...
                time_s <= t_bottom_s+time_margin_us*1e-6;
overview_gate = time_us >= 0 & ...
                time_us <= min(arrival_search_us(2),time_us(end));
depth_gate = time_s >= t_lateral_s & time_s <= t_bottom_s;
assert(nnz(depth_gate)>=2, 'Too few samples between lateral and back-wall times.');

env_ref = prctile(robust_envelope(overview_gate,:), 99.9, 'all');
envelope_db = 20*log10(max(robust_envelope/env_ref, realmin('double')));

depth_samples_mm = 0.5*sqrt((c*time_s(depth_gate)).^2-pcs^2)*1e3;
depth_axis_mm = linspace(depth_samples_mm(1), depth_samples_mm(end), 240)';
depth_image_db = interp1(depth_samples_mm, envelope_db(depth_gate,:), ...
                         depth_axis_mm, 'linear', NaN);

%% First-arrival detection at every scan position
noise_gate = time_us >= 0 & time_us < arrival_search_us(1);
arrival_gate = time_us >= arrival_search_us(1) & ...
               time_us <= min(arrival_search_us(2),time_us(end));
assert(nnz(noise_gate)>=5 && any(arrival_gate),'Invalid arrival/noise gate.');
hold_samples = max(2, round(arrival_hold_us*1e-6*fs));
first_arrival_us = nan(1,nscan);
for iscan = 1:nscan
    e = robust_envelope(:,iscan);
    nv = e(noise_gate);
    nmed = median(nv);
    nsigma = 1.4826*median(abs(nv-nmed));
    search_idx = find(arrival_gate);
    gv = e(search_idx);
    threshold = max(arrival_fraction*max(gv), nmed+arrival_noise_sigma*nsigma);
    above = gv >= threshold;
    onset = find(conv(double(above),ones(hold_samples,1),'valid')==hold_samples,1);
    if ~isempty(onset), first_arrival_us(iscan) = time_us(search_idx(onset)); end
end

outside_physical_window = isfinite(first_arrival_us) & ...
    (first_arrival_us < (t_lateral_s*1e6-time_margin_us) | ...
     first_arrival_us > (t_bottom_s*1e6+time_margin_us));
if any(outside_physical_window)
    warning(['%d/%d detected arrivals lie outside the expected TOFD window. ' ...
             'No time shift was applied; verify wedge/electronic delay and PCS.'], ...
            nnz(outside_physical_window),nnz(isfinite(first_arrival_us)));
end

%% Display
scan_index = 1:nscan;
rf_limit = prctile(abs(coherent_rf(overview_gate,:)),99.5,'all');
fig = figure('Color','w','Position',[60 60 1500 900]);
tiledlayout(2,2,'TileSpacing','compact','Padding','compact');

nexttile;
imagesc(scan_index,time_us(overview_gate),coherent_rf(overview_gate,:),[-rf_limit rf_limit]);
axis xy; colormap(gray); colorbar;
xlabel('Scan index'); ylabel('Time from transmit (\mus)');
title('Rx128 coherent RF overview (no alignment)');
yline(t_lateral_s*1e6,'--r','LW'); yline(t_bottom_s*1e6,'--c','BW');

nexttile;
imagesc(scan_index,time_us(overview_gate),envelope_db(overview_gate,:),db_range);
axis xy; colormap(gray); colorbar;
xlabel('Scan index'); ylabel('Time from transmit (\mus)');
title(sprintf('Rx128 robust envelope overview (%d to 0 dB)',db_range(1)));
yline(t_lateral_s*1e6,'--r','LW'); yline(t_bottom_s*1e6,'--c','BW');
hold on; plot(scan_index,first_arrival_us,'g.','MarkerSize',8); hold off;

nexttile;
imagesc(scan_index,depth_axis_mm,depth_image_db,db_range);
axis xy; colormap(gray); colorbar;
xlabel('Scan index'); ylabel('TOFD depth (mm)');
title('TOFD depth image (homogeneous symmetric model)');

nexttile;
plot(scan_index,first_arrival_us,'k.-'); grid on;
yline(t_lateral_s*1e6,'--r','LW'); yline(t_bottom_s*1e6,'--c','BW');
xlabel('Scan index'); ylabel('First-arrival time (\mus)');
title('Detected first arrival');

sgtitle(sprintf('Tx%d-Rx%d | PCS %.1f mm | thickness %.1f mm | %.1f-%.1f MHz', ...
        tx_no,rx_no,pcs*1e3,thickness*1e3,filter_band(1)/1e6,filter_band(2)/1e6));

%% Save to the application-owned output directory.
png_file = fullfile(output_dir,'tofd_analysis.png');
mat_file = fullfile(output_dir,'tofd_analysis.mat');
exportgraphics(fig,png_file,'Resolution',220);
save(mat_file,'single_frame_rf','coherent_rf','robust_envelope','envelope_db', ...
     'time_us','depth_samples_mm','depth_axis_mm','depth_image_db', ...
     'first_arrival_us','repeat_total','repeat_used','scan_dirs','data_root', ...
     'scan_names','scan_numbers','scan_frame_start','scan_frame_count', ...
     'compact_data_file','source_format','fs','c','pcs','thickness', ...
     'filter_band','tx_no','rx_no','n_rx_channels','-v7.3');

fprintf('\nExpected LW/BW: %.3f / %.3f us\n',t_lateral_s*1e6,t_bottom_s*1e6);
fprintf('Valid first arrivals: %d/%d positions\n',nnz(isfinite(first_arrival_us)),nscan);
fprintf('Saved: %s\n',png_file);
fprintf('Saved: %s\n',mat_file);

valid_arrivals = nnz(isfinite(first_arrival_us));
summary = struct( ...
    'data_type','tofd', ...
    'source_format',source_format, ...
    'scan_count',nscan, ...
    'valid_first_arrivals',valid_arrivals, ...
    'valid_arrival_ratio',valid_arrivals/max(nscan,1), ...
    'expected_lateral_wave_us',t_lateral_s*1e6, ...
    'expected_backwall_us',t_bottom_s*1e6, ...
    'tx_channel',tx_no, ...
    'rx_channel',rx_no, ...
    'sample_count',ns, ...
    'sampling_frequency_hz',fs, ...
    'pcs_mm',pcs*1e3, ...
    'plate_thickness_mm',thickness*1e3, ...
    'analysis_image','tofd_analysis.png', ...
    'analysis_mat','tofd_analysis.mat');
summary_file = fullfile(output_dir,'tofd_summary.json');
fid = fopen(summary_file,'w','n','UTF-8');
if fid < 0, error('Cannot create TOFD summary: %s',summary_file); end
cleanup_summary = onCleanup(@() fclose(fid)); %#ok<NASGU>
fwrite(fid,jsonencode(summary),'char');
result = summary;
end


function value = number_param(params,name,fallback)
value = fallback;
if isfield(params,name) && ~isempty(params.(name))
    parsed = str2double(string(params.(name)));
    if isfinite(parsed), value = double(parsed); end
end
end


function restore_figures(old_visibility)
close all force;
set(0,'DefaultFigureVisible',old_visibility);
end
