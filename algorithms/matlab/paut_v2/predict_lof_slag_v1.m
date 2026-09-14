function result=predict_lof_slag_v1(model_or_path,feature)
%PREDICT_LOF_SLAG_V1 Conservative LOF/large-strip-slag candidate decision.
% FEATURE must contain the seven common image features named in the model.
if ischar(model_or_path) || isstring(model_or_path)
    S=load(char(model_or_path),'model'); model=S.model;
else
    model=model_or_path;
end
if istable(feature)
    X=feature{:,model.feature_names};
elseif isstruct(feature)
    X=zeros(1,numel(model.feature_names));
    for i=1:numel(model.feature_names), X(i)=feature.(model.feature_names{i}); end
else
    X=feature;
end
classifier=model.large_strip_classifier;
Z=(X-classifier.mu)./classifier.sigma;
eta=[ones(size(Z,1),1),Z]*classifier.beta;
probability=1./(1+exp(-max(min(eta,35),-35)));
label=repmat("Indeterminate",numel(probability),1);
label(probability<=model.large_strip_lof_probability_max)="LOF";
label(probability>=model.large_strip_slag_probability_min)="Slag";
result=table(probability,label,'VariableNames',{'SlagProbability','Decision'});
end
