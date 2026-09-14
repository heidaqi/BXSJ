function result = predict_slag_large_v84(model_or_path,pca_major12_mm,depth_mm,position_class)
%PREDICT_SLAG_LARGE_V84 Apply the selected large-strip slag model.
if ischar(model_or_path) || isstring(model_or_path)
    S=load(char(model_or_path),'model'); model=S.model;
else
    model=model_or_path;
end
position_class=lower(string(position_class));
is_bevel=double(position_class=="bevel");
X=[log(max(pca_major12_mm,eps)),(depth_mm-22)/10,is_bevel];
Z=(X-model.major_model.mu)./model.major_model.sigma;
major=exp(model.major_model.beta(1)+Z*model.major_model.beta(2:end));
short_nominal=model.short_axis_nominal_mm;
short_range=model.short_axis_range_mm;
result=struct();
result.major_axis_mm=major;
result.short_axis_nominal_mm=short_nominal;
result.short_axis_range_mm=short_range;
result.area_nominal_mm2=pi*major*short_nominal/4;
result.area_range_mm2=pi*major*short_range/4;
result.position_class=position_class;
depth_tolerance=0;
if isfield(model,'measured_depth_tolerance_mm')
    depth_tolerance=model.measured_depth_tolerance_mm;
end
result.in_valid_depth_range=depth_mm>=model.valid_depth_range_mm(1)-depth_tolerance && ...
    depth_mm<=model.valid_depth_range_mm(2)+depth_tolerance;
prediction_range=model.valid_major_axis_range_mm;
if isfield(model,'valid_prediction_range_mm')
    prediction_range=model.valid_prediction_range_mm;
end
prediction_tolerance=0;
if isfield(model,'prediction_gate_tolerance_mm')
    prediction_tolerance=model.prediction_gate_tolerance_mm;
end
result.in_valid_major_range=major>=prediction_range(1)-prediction_tolerance && ...
    major<=prediction_range(2)+prediction_tolerance;
result.is_supported=result.in_valid_depth_range && result.in_valid_major_range && ...
    ismember(position_class,model.valid_position_classes);
result.warning=model.warning;
end
