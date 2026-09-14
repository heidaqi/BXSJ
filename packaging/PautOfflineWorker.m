function PautOfflineWorker(request_file)
% File-based bridge avoids quoting and Unicode loss in command-line JSON.
request = jsondecode(fileread(request_file));
parameters = jsonencode(request.parameters);
switch request.operation
    case 'Paut.m'
        Paut(request.input_dir, request.output_dir, parameters);
    case 'TOFD_complete_pipeline.m'
        TOFD_complete_pipeline(request.input_dir, request.output_dir, parameters);
    case 'DAS.m'
        DAS(request.input_dir, request.output_dir, parameters);
    case 'Ascan.m'
        Ascan(request.input_dir, request.output_dir, parameters);
    otherwise
        error('Unsupported imaging operation');
end
end
