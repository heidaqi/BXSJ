function features = extract_crack_multithreshold_features( ...
    envelope,x_img,z_img,peak_x,peak_z,thresholds_db)
%EXTRACT_CRACK_MULTITHRESHOLD_FEATURES 裂纹训练/推理共享的多阈值PCA特征。

[~,peak_col]=min(abs(x_img-peak_x));
[~,peak_row]=min(abs(z_img-peak_z));
seed=sub2ind(size(envelope),peak_row,peak_col);
reference_peak=max(envelope(:));
db=20*log10(envelope/max(reference_peak,realmin)+eps);
features=repmat(empty_feature(),1,numel(thresholds_db));
dx=median(diff(x_img)); dz=median(diff(z_img));
for k=1:numel(thresholds_db)
    mask=component_at_seed(db>=thresholds_db(k),seed);
    [rows,cols]=find(mask);
    f=empty_feature(); f.threshold_db=thresholds_db(k); f.mask=mask;
    if isempty(rows), features(k)=f; continue; end
    points=[x_img(cols(:)).',z_img(rows(:)).'];
    center=mean(points,1); centered=points-center;
    covariance=(centered'*centered)/max(size(centered,1),1);
    [vectors,values]=eig(covariance);
    [~,order]=sort(diag(values),'descend');
    major=vectors(:,order(1));
    if major(1)<0, major=-major; end
    minor=[-major(2);major(1)];
    major_projection=centered*major;
    minor_projection=centered*minor;
    p1=center+min(major_projection)*major';
    p2=center+max(major_projection)*major';
    midpoint=(p1+p2)/2;
    f.length_mm=max(major_projection)-min(major_projection)+min(dx,dz);
    f.minor_length_mm=max(minor_projection)-min(minor_projection)+min(dx,dz);
    f.area_mm2=nnz(mask)*dx*dz;
    f.endpoint1_x_mm=p1(1); f.endpoint1_z_mm=p1(2);
    f.endpoint2_x_mm=p2(1); f.endpoint2_z_mm=p2(2);
    f.midpoint_x_mm=midpoint(1); f.midpoint_z_mm=midpoint(2);
    f.angle_deg=atan2d(major(2),major(1));
    f.aspect_ratio=f.length_mm/max(f.minor_length_mm,eps);
    features(k)=f;
end
end

function component=component_at_seed(mask,seed)
component=false(size(mask)); cc=bwconncomp(mask,8);
for k=1:cc.NumObjects
    if any(cc.PixelIdxList{k}==seed)
        component(cc.PixelIdxList{k})=true; return;
    end
end
end

function f=empty_feature()
f=struct('threshold_db',NaN,'length_mm',NaN,'minor_length_mm',NaN, ...
    'area_mm2',NaN,'endpoint1_x_mm',NaN,'endpoint1_z_mm',NaN, ...
    'endpoint2_x_mm',NaN,'endpoint2_z_mm',NaN,'midpoint_x_mm',NaN, ...
    'midpoint_z_mm',NaN,'angle_deg',NaN,'aspect_ratio',NaN,'mask',[]);
end
