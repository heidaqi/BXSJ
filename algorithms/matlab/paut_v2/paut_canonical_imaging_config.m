function imaging = paut_canonical_imaging_config()
%PAUT_CANONICAL_IMAGING_CONFIG Single source of truth for PAUT DAS imaging.
imaging.velocity_mps=5900;
imaging.tx_z_mm=0;
imaging.rx_z_mm=0;
imaging.probe_positions_mm=[30,32,34,36,38,40,42,44,49,51,53,55,57,59,61,63];
imaging.x_img=20:0.2:80;
imaging.z_img=0:0.2:40;
imaging.bandpass_hz=[1.5e6,4.0e6];
imaging.filter_order=4;
imaging.gate_start_us=0.8;
imaging.gate_end_us=14.5;
imaging.gate_transition_us=0.25;
imaging.gaussian_sigma_mm=0.4;
imaging.db_floor=-35;
end
