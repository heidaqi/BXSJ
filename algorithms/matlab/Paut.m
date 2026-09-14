function result = Paut(data_folder, save_folder, params_json)
%PAUT Stable application entry for the V2 PAUT imaging and analysis pipeline.
% Keep this public signature unchanged: Python and compiled integrations call it.

if nargin < 1, data_folder = ''; end
if nargin < 2, save_folder = ''; end
if nargin < 3 || isempty(params_json), params_json = '{}'; end

params = jsondecode(char(params_json));
this_dir = fileparts(mfilename('fullpath'));
pipeline_dir = fullfile(this_dir, 'paut_v2');
if ~isdeployed
    addpath(pipeline_dir);
    cleanup_path = onCleanup(@() rmpath(pipeline_dir)); %#ok<NASGU>
end

cfg = struct();
cfg = copy_scalar(params, cfg, 'velocity_mps', 'velocity_mps');
cfg = copy_scalar(params, cfg, 'sample_rate_hz', 'expected_sample_rate_hz');
cfg = copy_scalar(params, cfg, 'gaussian_sigma_mm', 'gaussian_sigma_mm');
cfg = copy_scalar(params, cfg, 'display_threshold_db', 'display_threshold_db');
cfg = copy_scalar(params, cfg, 'display_background_db', 'display_background_db');
cfg = copy_scalar(params, cfg, 'background_scale', 'background_scale');

step = numeric_param(params, 'pixel_step_mm', 0.2);
x_min = numeric_param(params, 'x_min_mm', 20);
x_max = numeric_param(params, 'x_max_mm', 80);
z_min = numeric_param(params, 'z_min_mm', 0);
z_max = numeric_param(params, 'z_max_mm', 40);
cfg.x_img = x_min:step:x_max;
cfg.z_img = z_min:step:z_max;

positions = vector_param(params, 'tx_positions_mm');
if ~isempty(positions), cfg.nested_probe_positions_mm = positions; end

runtime_fields = {'python_executable','inference_mode','inference_script', ...
    'background_input_path', ...
    'crack_cnn_model','crack_length_model','refined_crack_model', ...
    'lof_length_model','pore_area_model','slag_area_model', ...
    'lof_slag_classifier_model','forced_defect_type'};
for k = 1:numel(runtime_fields)
    name = runtime_fields{k};
    if isfield(params,name) && ~isempty(params.(name))
        cfg.(name) = char(string(params.(name)));
    end
end
cfg = copy_scalar(params, cfg, 'enable_advisory_slag_quantification', ...
    'enable_advisory_slag_quantification');

old_visibility = get(0, 'DefaultFigureVisible');
set(0, 'DefaultFigureVisible', 'off');
cleanup_figures = onCleanup(@() restore_figures(old_visibility)); %#ok<NASGU>

[report, image_result] = PAUT_complete_pipeline(data_folder, save_folder, cfg);
result = struct( ...
    'report', report, ...
    'image_result', image_result, ...
    'default_image', fullfile(save_folder, 'das_image_thresholded.png'), ...
    'original_image', fullfile(save_folder, 'das_image_full.png'), ...
    'report_csv', fullfile(save_folder, 'defect_report.csv'), ...
    'analysis_mat', fullfile(save_folder, 'analysis_result.mat'));
end


function cfg = copy_scalar(params, cfg, source_name, target_name)
if isfield(params,source_name) && ~isempty(params.(source_name))
    cfg.(target_name) = double(params.(source_name));
end
end


function value = numeric_param(params, name, fallback)
value = fallback;
if isfield(params,name) && ~isempty(params.(name))
    parsed = str2double(string(params.(name)));
    if isfinite(parsed), value = parsed; end
end
end


function values = vector_param(params, name)
values = [];
if ~isfield(params,name) || isempty(params.(name)), return; end
raw = params.(name);
if isnumeric(raw)
    values = double(raw(:)).';
else
    values = sscanf(strrep(char(string(raw)), ',', ' '), '%f').';
end
values = values(isfinite(values));
end


function restore_figures(old_visibility)
close all force;
set(0, 'DefaultFigureVisible', old_visibility);
end
