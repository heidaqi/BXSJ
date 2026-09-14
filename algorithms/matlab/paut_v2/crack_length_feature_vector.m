function X = crack_length_feature_vector(variant,lengths,estimated_angle,depth_mm,global_prediction)
%CRACK_LENGTH_FEATURE_VECTOR 裂纹长度模型的训练/推理共享特征构造。
if nargin<5, global_prediction=[]; end
variant=string(variant);
l6=lengths(:,1); l10=lengths(:,2); l12=lengths(:,3);
delta10=l10-l6; delta12=l12-l6;
switch variant
    case "L6线性"
        X=l6;
    case "L6+扩张+角度+深度"
        X=[l6,delta10,estimated_angle,depth_mm];
    case "L6二次+扩张+角度+深度"
        X=[l6,l6.^2,delta10,estimated_angle,depth_mm];
    case "L6+双阈值扩张+角度+深度"
        X=[l6,delta10,delta12,estimated_angle,depth_mm];
    case "短裂纹残差+二次深度"
        if isempty(global_prediction), error('短裂纹残差特征需要全局预测长度。'); end
        depth_offset=depth_mm-20;
        angle_weight=(90-estimated_angle)/30;
        X=[global_prediction,delta10,angle_weight,depth_offset,depth_offset.^2, ...
            angle_weight.*depth_offset,angle_weight.*depth_offset.^2];
    case "长裂纹残差"
        if isempty(global_prediction), error('长裂纹残差特征需要全局预测长度。'); end
        angle_offset=(estimated_angle(:)-70)/20;
        depth_offset=(depth_mm(:)-20)/10;
        X=[global_prediction(:),delta10,delta12,angle_offset,depth_offset];
    otherwise
        X=[l6,l10,l12,estimated_angle,depth_mm];
end
end
