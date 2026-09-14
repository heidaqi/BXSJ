import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity,
  CheckCircle2,
  Download,
  FileImage,
  FileText,
  FolderOpen,
  Lightbulb,
  Maximize2,
  Play,
  PlusSquare,
  RefreshCw,
  Ruler,
  Save,
  Search,
  Server,
  Settings2,
  Trash2,
  UploadCloud,
  X
} from 'lucide-react';
import './styles.css';

const API = import.meta.env.VITE_API_BASE
  || (window.location.port === '5173' ? 'http://127.0.0.1:8000' : window.location.origin);

class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('界面渲染失败', error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="appRenderError" role="alert">
        <section>
          <h1>界面显示异常</h1>
          <p>检测数据仍保存在本地。请重新加载界面；如果问题持续出现，请将日志交给开发人员。</p>
          <button className="primary" onClick={() => window.location.reload()}>重新加载</button>
        </section>
      </main>
    );
  }
}

function imagePixelFromEvent(event, imageElement, imageWidth, imageHeight, clamp = false) {
  if (!imageElement || !imageWidth || !imageHeight) return null;
  const rect = imageElement.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return null;
  const localX = event.clientX - rect.left;
  const localY = event.clientY - rect.top;
  if (!clamp && (localX < 0 || localY < 0 || localX > rect.width || localY > rect.height)) {
    return null;
  }
  const safeX = Math.max(0, Math.min(rect.width, localX));
  const safeY = Math.max(0, Math.min(rect.height, localY));
  return {
    x: (safeX / rect.width) * imageWidth,
    y: (safeY / rect.height) * imageHeight,
    markerLeft: event.clientX - rect.left,
    markerTop: event.clientY - rect.top,
  };
}

function App() {
  const [jobs, setJobs] = useState([]);
  const [active, setActive] = useState(null);
  const [busy, setBusy] = useState(false);
  const [actionName, setActionName] = useState('');
  const [message, setMessage] = useState('');
  const [agentResult, setAgentResult] = useState(null);
  const [agentStatus, setAgentStatus] = useState('未开始');
  const agentStatusRef = useRef('未开始');
  const [realtimeImagePreview, setRealtimeImagePreview] = useState('');
  const [selectedImageId, setSelectedImageId] = useState('');
  const [selectedDefectId, setSelectedDefectId] = useState('');
  const [activeSection, setActiveSection] = useState('realtime');
  const [realtimeStatus, setRealtimeStatus] = useState(null);
  const [cacheStatus, setCacheStatus] = useState(null);
  const [realtimeFrame, setRealtimeFrame] = useState(null);
  const [realtimePath, setRealtimePath] = useState('');
  const [realtimeInterval, setRealtimeInterval] = useState(0);
  const [simulationEnabled, setSimulationEnabled] = useState(false);
  const [vehicleSpeed, setVehicleSpeed] = useState(0);
  const [showImagingParams, setShowImagingParams] = useState(false);
  const [imagingParams, setImagingParams] = useState({
    velocity_mps: 5900,
    sample_rate_mhz: 50,
    plate_thickness_mm: 40,
    x_min_mm: 20,
    x_max_mm: 80,
    z_min_mm: 0,
    z_max_mm: 40,
    pixel_step_mm: 0.2,
    dynamic_range_db: 20,
    gaussian_smoothing_sigma_mm: 0.4,
  });
  const [realtimeBusy, setRealtimeBusy] = useState(false);
  const [realtimeSessions, setRealtimeSessions] = useState([]);
  const [replaySession, setReplaySession] = useState(null);
  const [replayFrames, setReplayFrames] = useState([]);
  const [replayIndex, setReplayIndex] = useState(0);
  const [followRealtime, setFollowRealtime] = useState(true);
  const [advancedReviewEnabled, setAdvancedReviewEnabled] = useState(false);
  const [realtimeFocus, setRealtimeFocus] = useState(null);
  const [realtimeWaveform, setRealtimeWaveform] = useState(null);
  const [waveformBusy, setWaveformBusy] = useState(false);
  const [realtimePickMarker, setRealtimePickMarker] = useState(null);
  const resumeAfterAdvancedReviewRef = useRef(false);
  const handedOffJobRef = useRef('');
  const currentSessionRef = useRef('');
  const statusGenerationRef = useRef(0);
  const [validatedSource, setValidatedSource] = useState('');
  const [reviewFilter, setReviewFilter] = useState('待复核');
  const [reviewQuery, setReviewQuery] = useState('');
  const [reviewPage, setReviewPage] = useState(1);
  const calibrationImageRef = useRef(null);
  const reviewImageRef = useRef(null);
  const realtimeImageRef = useRef(null);
  const [manualDrawEnabled, setManualDrawEnabled] = useState(false);
  const [draftBox, setDraftBox] = useState(null);
  const [pautZipFile, setPautZipFile] = useState(null);
  const [pautBusy, setPautBusy] = useState(false);  
  const [pautImageView, setPautImageView] = useState('thresholded');
  const [reviewImageView, setReviewImageView] = useState('thresholded');
  const [tofdZipFile, setTofdZipFile] = useState(null);
  const [tofdBusy, setTofdBusy] = useState(false);
  const [tofdResult, setTofdResult] = useState(null);
  const [tofdLocalDir, setTofdLocalDir] = useState('');
  const [tofdParams, setTofdParams] = useState({
    velocity_mps: 5900,
    plate_thickness_mm: 6.3,
    tofd_pcs_mm: 42,
    tofd_tx_channel: 1,
    tofd_rx_channel: 128,
    tofd_header_bytes: 128,
  });
  const [desktopBridge, setDesktopBridge] = useState(null);
  const [capabilities, setCapabilities] = useState(null);
  const [appSettings, setAppSettings] = useState({
    agent_enabled: false,
    openai_base_url: 'https://api.openai.com/v1',
    openai_model: 'gpt-4o-mini',
    openai_api_key: '',
    matlab_exe: 'matlab.exe',
    matlab_runtime_module: '',
    output_dir: ''
  });
  const [isDrawingBox, setIsDrawingBox] = useState(false);
  const [calibration, setCalibration] = useState({
    scan_area: '',
    probe_or_channel: '',
    image_note: '',
    calibration_method: '未标定',
    mm_per_pixel: '',
    scale_distance_mm: '',
    point_a_x: '',
    point_a_y: '',
    point_b_x: '',
    point_b_y: '',
    pickingPoint: '',
    calibration_note: ''
  });
  const [defectForm, setDefectForm] = useState({
    class_name: '人工标注缺陷',
    x_min: '',
    y_min: '',
    x_max: '',
    y_max: '',
    suggestion: '',
    remark: ''
  });
  const busyText = actionName ? `${actionName}中...` : '处理中...';

  useEffect(() => {
    loadJobs();
    loadAppSettings();
    loadCapabilities();
  }, []);

  useEffect(() => {
    agentStatusRef.current = agentStatus;
  }, [agentStatus]);

  useEffect(() => {
    if (window.qt?.webChannelTransport && window.QWebChannel) {
      new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
        setDesktopBridge(channel.objects.desktopBridge || null);
      });
    }
  }, []);

  const followRealtimeRef = useRef(followRealtime);
  useEffect(() => {
    followRealtimeRef.current = followRealtime;
  }, [followRealtime]);

  useEffect(() => {
    if (activeSection !== 'realtime') return undefined;
    loadRealtimeStatus();
    const timer = window.setInterval(loadRealtimeStatus, 2500);
    return () => window.clearInterval(timer);
  }, [activeSection]);

  useEffect(() => {
    if (activeSection !== 'realtime') return undefined;
    let ws = null;
    let reconnectTimer = null;
    let closedByCleanup = false;

    const connect = () => {
      const wsUrl = API.replace(/^http/, 'ws') + '/api/realtime/ws';
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        // 连接后兜底拉取一次最新帧，补齐断线期间可能错过的帧
        fetch(`${API}/api/realtime/frames/latest`)
          .then((r) => (r.ok ? r.json() : null))
          .then((f) => {
            if (f && followRealtimeRef.current && (!currentSessionRef.current || f.session_id === currentSessionRef.current)) setRealtimeFrame(f);
          })
          .catch(() => {});
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'frame' && msg.frame && followRealtimeRef.current) {
            if (currentSessionRef.current && msg.frame.session_id !== currentSessionRef.current) return;
            setRealtimeFrame(msg.frame);
          } else if (msg.type === 'status' && msg.status) {
            if (currentSessionRef.current && msg.status.session_id !== currentSessionRef.current) return;
            setRealtimeStatus(msg.status);
          }
        } catch (e) {
          // 忽略无法解析的消息
        }
      };

      ws.onclose = () => {
        if (!closedByCleanup) {
          reconnectTimer = window.setTimeout(connect, 1000);
        }
      };

      ws.onerror = () => {
        if (ws) ws.close();
      };
    };

    connect();

    return () => {
      closedByCleanup = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [activeSection]);

  useEffect(() => {
    setRealtimeFocus(null);
    setRealtimeWaveform(null);
    setRealtimePickMarker(null);
  }, [realtimeFrame?.frame_id]);

  const selectedImage = useMemo(() => {
    if (!active?.images?.length) return null;
    return active.images.find((item) => item.id === selectedImageId) || active.images[0];
  }, [active, selectedImageId]);

  useEffect(() => {
    if (!selectedImage) return;
    const savedCalibration = active?.batch_calibration || selectedImage;
    const pointsBelongToImage = !savedCalibration.source_image_id || savedCalibration.source_image_id === selectedImage.id;
    setReviewImageView(selectedImage.default_view || 'thresholded');
    setCalibration({
      scan_area: savedCalibration.scan_area || selectedImage.area_label || '',
      probe_or_channel: savedCalibration.probe_or_channel || '',
      image_note: savedCalibration.image_note || '',
      calibration_method: savedCalibration.calibration_method || (savedCalibration.mm_per_pixel ? '手动比例' : '未标定'),
      mm_per_pixel: savedCalibration.mm_per_pixel ?? '',
      scale_distance_mm: pointsBelongToImage ? (savedCalibration.scale_distance_mm ?? '') : '',
      point_a_x: pointsBelongToImage ? (savedCalibration.point_a_x ?? '') : '',
      point_a_y: pointsBelongToImage ? (savedCalibration.point_a_y ?? '') : '',
      point_b_x: pointsBelongToImage ? (savedCalibration.point_b_x ?? '') : '',
      point_b_y: pointsBelongToImage ? (savedCalibration.point_b_y ?? '') : '',
      pickingPoint: '',
      calibration_note: savedCalibration.calibration_note || ''
    });
    setDraftBox(null);
    setIsDrawingBox(false);
    setManualDrawEnabled(false);
  }, [selectedImage, active?.batch_calibration]);

  const selectedDefect = useMemo(() => {
    if (!active?.defects?.length) return null;
    return active.defects.find((item) => item.id === selectedDefectId) || null;
  }, [active, selectedDefectId]);

  useEffect(() => {
    if (!selectedDefect) return;
    setDefectForm({
      class_name: selectedDefect.class_name || '',
      x_min: selectedDefect.x_min ?? '',
      y_min: selectedDefect.y_min ?? '',
      x_max: selectedDefect.x_max ?? '',
      y_max: selectedDefect.y_max ?? '',
      suggestion: selectedDefect.suggestion || '',
      remark: selectedDefect.remark || ''
    });
  }, [selectedDefect]);

  async function loadJobs() {
    const res = await fetch(`${API}/api/jobs`);
    const data = await res.json();
    setJobs(data.items || []);
  }

  async function loadRealtimeStatus() {
    const generation = statusGenerationRef.current;
    try {
      const [statusResponse, cacheResponse, sessionsResponse] = await Promise.all([
        fetch(`${API}/api/realtime/status`),
        fetch(`${API}/api/realtime/cache/status`),
        fetch(`${API}/api/realtime/sessions?limit=30`)
      ]);
      if (!statusResponse.ok) throw new Error(await readApiError(statusResponse));
      const status = await statusResponse.json();
      if (generation !== statusGenerationRef.current) return;
      if (currentSessionRef.current && status.session_id !== currentSessionRef.current) return;
      setRealtimeStatus(status);
      if (status.handoff_job_id && handedOffJobRef.current !== status.handoff_job_id) {
        handedOffJobRef.current = status.handoff_job_id;
        await loadJob(status.handoff_job_id, 'review');
        setMessage(`本次实时处理已归档，共 ${status.session_frame_count || 0} 帧；可在结果复核和报告与 Agent 页面继续处理`);
      }
      if (cacheResponse.ok) setCacheStatus(await cacheResponse.json());
      if (sessionsResponse.ok) setRealtimeSessions((await sessionsResponse.json()).items || []);
    } catch (error) {
      setMessage(`实时状态读取失败：${error.message}`);
    }
  }

  async function inspectRealtimePoint(event) {
    if (!advancedReviewEnabled || !realtimeFrame?.frame_id || !realtimeImageRef.current) return;
    if (event.button !== undefined && event.button !== 0) return;
    event.preventDefault();
    const frameId = realtimeFrame.frame_id;
    const image = realtimeImageRef.current;
    const naturalWidth = image.naturalWidth || image.getBoundingClientRect().width;
    const naturalHeight = image.naturalHeight || image.getBoundingClientRect().height;
    const point = imagePixelFromEvent(event, image, naturalWidth, naturalHeight, false);
    if (!point) {
      setMessage('请点击DAS图像的彩色成像区域，不要点击两侧留白');
      return;
    }
    const xRatio = point.x / naturalWidth;
    const zRatio = point.y / naturalHeight;
    const [xMin, xMax] = realtimeFrame.image.x_range_mm;
    const [zMin, zMax] = realtimeFrame.image.z_range_mm;
    setRealtimePickMarker({ left: point.markerLeft, top: point.markerTop });
    setWaveformBusy(true);
    setMessage('已接收复核落点，正在反查相关A扫通道...');
    try {
      const response = await fetch(`${API}/api/realtime/frames/${frameId}/focus`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ x_mm: xMin + xRatio * (xMax - xMin), z_mm: zMin + zRatio * (zMax - zMin), top_k: 8 })
      });
      if (!response.ok) throw new Error(await readApiError(response));
      const focus = await response.json();
      setRealtimeFocus(focus);
      if (focus.channels?.length) {
        await loadRealtimeWaveform(focus.channels[0].tx_index, focus.channels[0].rx_index, frameId);
        setMessage(`已定位 x=${focus.x_mm} mm、z=${focus.z_mm} mm，并加载相关A扫通道`);
      } else {
        setRealtimeWaveform(null);
        setMessage('该位置没有可用的相关A扫通道');
      }
    } catch (error) {
      setMessage(`DAS反查失败：${error.message}`);
    } finally {
      setWaveformBusy(false);
    }
  }

  function toggleAdvancedReview() {
    const enabled = !advancedReviewEnabled;
    setAdvancedReviewEnabled(enabled);
    if (enabled) {
      resumeAfterAdvancedReviewRef.current = followRealtime;
      setFollowRealtime(false);
    } else {
      setRealtimeFocus(null);
      setRealtimeWaveform(null);
      setRealtimePickMarker(null);
      if (resumeAfterAdvancedReviewRef.current) setFollowRealtime(true);
      resumeAfterAdvancedReviewRef.current = false;
    }
    setMessage(enabled ? '高级复核已开启，当前画面已锁定，可点击DAS图像查看相关A扫通道' : '高级复核已关闭');
  }

  async function loadRealtimeWaveform(txIndex, rxIndex, frameId = realtimeFrame?.frame_id) {
    if (!frameId) return;
    setWaveformBusy(true);
    try {
      const response = await fetch(`${API}/api/realtime/frames/${frameId}/waveform?tx=${txIndex}&rx=${rxIndex}&max_points=1200`);
      if (!response.ok) throw new Error(await readApiError(response));
      setRealtimeWaveform(await response.json());
    } catch (error) {
      setMessage(`A扫波形读取失败：${error.message}`);
    } finally {
      setWaveformBusy(false);
    }
  }

  async function generateAgentReport() {
    if (!active?.job?.id) {
      setMessage('请先选择一个已完成的检测批次');
      return;
    }
    let destinationPath = '';
    if (desktopBridge) {
      const suggestedName = `${active.job.batch_name || 'PAUT检测'}_检测报告.pdf`;
      destinationPath = await new Promise((resolve) => desktopBridge.selectReportSavePath(suggestedName, resolve));
      if (!destinationPath) {
        setMessage('已取消生成报告');
        return;
      }
    }
    setBusy(true);
    setActionName('生成报告');
    setMessage('正在整理A扫分析结果并生成报告...');
    try {
      const realtimeBatchId = active.job.source_type === 'realtime' ? active.job.source_batch_id : '';
      const endpoint = realtimeBatchId
        ? `${API}/api/realtime/batches/${realtimeBatchId}/report`
        : `${API}/api/jobs/${active.job.id}/report`;
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(destinationPath ? { destination_path: destinationPath } : {})
      });
      if (!response.ok) throw new Error(await readApiError(response));
      const result = await response.json();
      if (desktopBridge && result.report_path) {
        desktopBridge.openPath(result.report_path, (opened) => {
          setMessage(opened ? `报告已保存并打开：${result.report_path}` : `报告已保存：${result.report_path}`);
        });
      } else if (result.report_url) {
        window.open(`${API}${result.report_url}`, '_blank', 'noopener,noreferrer');
        setMessage('报告已生成，请在浏览器下载提示中选择保存位置');
      } else {
        setMessage(`报告已生成：${result.report_path || '保存完成'}`);
      }
    } catch (error) {
      setMessage(`报告生成失败：${error.message}`);
    } finally {
      setActionName('');
      setBusy(false);
    }
  }

  function resumeRealtimeFollowing() {
    setFollowRealtime(true);
    setMessage('已恢复跟随最新实时帧');
    loadRealtimeStatus();
  }

  // 选择 ZIP 文件
  function handlePautZipSelect(event) {
    const file = event.target.files?.[0];
    if (file && file.name.endsWith('.zip')) {
      setPautZipFile(file);
      setMessage(`已选择 ZIP：${file.name}`);
    } else {
      setPautZipFile(null);
      setMessage('请选择 .zip 压缩包');
    }
  }

// 上传 ZIP 并自动执行 PAUT 检测
// 上传 ZIP 并配置实时监控
  async function uploadPautZip() {
    if (!pautZipFile) {
      setMessage('请先选择 ZIP 文件');
      return;
    }

    const jobId = `job_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
    
    setPautBusy(true);
    setMessage('正在上传 PAUT 数据...');

    const formData = new FormData();
    formData.append('file', pautZipFile);
    formData.append('batch_name', `PAUT检测-${new Date().toISOString().slice(0,10)}`);

    try {
      // 1. 上传 ZIP（只上传，不检测）
      const uploadRes = await fetch(`${API}/api/paut/jobs/${jobId}/upload`, {
        method: 'POST',
        body: formData,
      });
      if (!uploadRes.ok) throw new Error(await uploadRes.text());
      const uploadData = await uploadRes.json();
      setMessage(`PAUT 数据上传成功：${uploadData.txt_count} 个 .txt 文件`);

      // 2. 配置实时监控指向这个 job 的数据
      const configRes = await fetch(`${API}/api/realtime/config/job/${jobId}`, {
        method: 'POST',
      });
      if (!configRes.ok) {
        const errText = await configRes.text();
        throw new Error(`配置实时监控失败：${errText}`);
      }
      const configData = await configRes.json();
      setMessage(`数据已加载到实时监控，点击"开始处理"执行DAS成像`);

      // 3. 新数据源进入监控后不激活旧业务批次，停止归档后再自动绑定。
      setActive(null);
      setSelectedImageId('');
      setSelectedDefectId('');
      setAgentResult(null);
      setAgentStatus('未开始');
      await loadJobs();

      // 4. 自动切换到实时监控页面
      setActiveSection('realtime');
      setPautZipFile(null);

      // 5. 强制刷新实时状态
      await loadRealtimeStatus();

    } catch (error) {
      setMessage(`PAUT 上传失败：${error.message}`);
    } finally {
      setPautBusy(false);
    }
  }

  function handleTofdZipSelect(event) {
    const file = event.target.files?.[0];
    const lowerName = file?.name?.toLowerCase() || '';
    if (file && (lowerName.endsWith('.mat') || lowerName.endsWith('.zip'))) {
      setTofdZipFile(file);
      setMessage(`已选择 TOFD 数据：${file.name}`);
    } else {
      setTofdZipFile(null);
      setMessage('请选择 Tx1→Rx128 紧凑 MAT 文件，或原始 TOFD ZIP 数据包');
    }
  }

  async function uploadAndAnalyzeTofd() {
    if (!tofdZipFile) return;
    const jobId = `tofd_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
    const formData = new FormData();
    formData.append('file', tofdZipFile);
    formData.append('batch_name', `TOFD检测-${new Date().toISOString().slice(0, 10)}`);
    setTofdBusy(true);
    setTofdResult(null);
    setMessage('正在导入并校验 TOFD 数据...');
    try {
      const uploadResponse = await fetch(`${API}/api/tofd/jobs/${jobId}/upload`, { method: 'POST', body: formData });
      if (!uploadResponse.ok) throw new Error(await uploadResponse.text());
      setMessage('TOFD 数据结构校验完成，正在成像和分析...');
      const detectResponse = await fetch(`${API}/api/tofd/jobs/${jobId}/detect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(tofdParams),
      });
      if (!detectResponse.ok) throw new Error(await detectResponse.text());
      const result = await detectResponse.json();
      setTofdResult(result);
      setTofdZipFile(null);
      setMessage(`TOFD 成像完成：共 ${result.analysis?.scan_count || 0} 个扫描位置`);
      await loadJobs();
    } catch (error) {
      setMessage(`TOFD 处理失败：${error.message}`);
    } finally {
      setTofdBusy(false);
    }
  }

  function updateTofdParam(key, value) {
    setTofdParams((prev) => ({ ...prev, [key]: value === '' ? '' : Number(value) }));
  }

  async function analyzeTofdFromLocalDir() {
    if (!tofdLocalDir.trim()) return;
    const jobId = `tofd_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
    setTofdBusy(true);
    setTofdResult(null);
    setMessage('正在读取本地 TOFD 数据并成像...');
    try {
      const response = await fetch(`${API}/api/tofd/local-detect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          job_id: jobId,
          batch_name: 'TOFD本地检测',
          data_dir: tofdLocalDir.trim(),
          ...tofdParams,
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      const result = await response.json();
      setTofdResult(result);
      setMessage(`TOFD 成像完成：共 ${result.analysis?.scan_count || 0} 个扫描位置`);
      await loadJobs();
    } catch (error) {
      setMessage(`TOFD 处理失败：${error.message}`);
    } finally {
      setTofdBusy(false);
    }
  }
  function pauseRealtimeFollowing() {
    setFollowRealtime(false);
    setMessage('实时画面已暂停，后台仍继续采集和分析');
  }

  function updateImagingParam(key, value) {
    setImagingParams((prev) => ({ ...prev, [key]: value === '' ? '' : Number(value) }));
  }

  async function configureRealtime() {
    statusGenerationRef.current += 1;
    if (!realtimePath.trim()) {
      setMessage('请输入A扫数据目录（可直接填多组数据文件夹所在的父目录）');
      return;
    }
    setRealtimeBusy(true);
    setMessage('正在校验A扫数据目录...');
    try {
      const response = await fetch(`${API}/api/realtime/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_path: realtimePath.trim(),
          profile: 'comsol_16x16_das_m',
          interval_seconds: Math.max(0, Number(realtimeInterval) || 0),
          simulation_enabled: simulationEnabled,
          vehicle_speed_mm_s: Number(vehicleSpeed) || 0,
          velocity_mps: imagingParams.velocity_mps,
          sample_rate_hz: Number(imagingParams.sample_rate_mhz) * 1e6,
          plate_thickness_mm: imagingParams.plate_thickness_mm,
          data_type: 'paut',
          x_min_mm: imagingParams.x_min_mm,
          x_max_mm: imagingParams.x_max_mm,
          z_min_mm: imagingParams.z_min_mm,
          z_max_mm: imagingParams.z_max_mm,
          pixel_step_mm: imagingParams.pixel_step_mm,
          dynamic_range_db: imagingParams.dynamic_range_db,
          gaussian_smoothing_sigma_mm: imagingParams.gaussian_smoothing_sigma_mm,
        })
      });
      if (!response.ok) throw new Error(await response.text());
      const configured = await response.json();
      currentSessionRef.current = configured.session_id;
      statusGenerationRef.current += 1;
      setRealtimeStatus(configured);
      setValidatedSource(realtimePath.trim());
      setRealtimeFrame(null);
      setRealtimeFocus(null);
      setRealtimeWaveform(null);
      setRealtimePickMarker(null);
      setAdvancedReviewEnabled(false);
      setFollowRealtime(true);
      handedOffJobRef.current = '';
      setActive(null);
      setSelectedImageId('');
      setSelectedDefectId('');
      setAgentResult(null);
      setAgentStatus('未开始');
      setMessage('16发16收 COMSOL 数据源配置完成，成像参数已下发，可以开始批量处理');
    } catch (error) {
      setMessage(`实时配置失败：${error.message}`);
    } finally {
      setRealtimeBusy(false);
    }
  }

  async function controlRealtime(action) {
    setRealtimeBusy(true);
    setMessage(action === 'start' ? '正在启动批量流式处理...' : '正在停止数据处理...');
    try {
      const response = await fetch(`${API}/api/realtime/${action}`, { method: 'POST' });
      if (!response.ok) throw new Error(await response.text());
      const status = await response.json();
      setRealtimeStatus(status);
      if (action === 'start') {
        handedOffJobRef.current = '';
        setMessage(`处理批次 ${status.session_id || ''} 已开启，后端会逐组DAS成像并自动保存`);
      } else if (status.handoff_job_id) {
        handedOffJobRef.current = status.handoff_job_id;
        await loadJob(status.handoff_job_id, 'review');
        setMessage(`处理已结束并归档 ${status.session_frame_count || 0} 帧，已进入结果复核`);
      } else {
        setMessage(status.message || '正在完成当前数据组，完成后将自动进入结果复核');
      }
    } catch (error) {
      setMessage(`实时操作失败：${error.message}`);
    } finally {
      setRealtimeBusy(false);
    }
  }

  async function openRealtimeReplay(session) {
    try {
      const response = await fetch(`${API}/api/realtime/sessions/${session.session_id}/frames`);
      if (!response.ok) throw new Error(await readApiError(response));
      const frames = (await response.json()).items || [];
      setReplaySession(session);
      setReplayFrames(frames);
      setReplayIndex(Math.max(0, frames.length - 1));
    } catch (error) {
      setMessage(`批次复盘读取失败：${error.message}`);
    }
  }

  useEffect(() => {
    const jobId = active?.job?.id;
    if (activeSection !== 'report' || !jobId || active?.job?.source_type !== 'realtime') return undefined;
    let cancelled = false;
    let requestRunning = false;
    let failureCount = 0;
    const refresh = async () => {
      if (requestRunning) return;
      requestRunning = true;
      try {
        const response = await fetch(`${API}/api/jobs/${jobId}/agent/result`);
        if (!response.ok || cancelled) return;
        const data = await response.json();
        if (cancelled) return;
        const previousStatus = agentStatusRef.current;
        applyAgentResult(data, false);
        const finished = ['分析完成', '本地摘要可用', 'Agent失败，本地摘要可用', '大模型调用失败，本地摘要可用'].includes(data.status);
        if (finished && ['排队中', '分析中', '提交中'].includes(previousStatus)) {
          setMessage(data.status === '分析完成' ? 'Agent分析完成，结果已自动更新' : '分析已完成，当前显示可用的本地分析结果');
        }
        failureCount = 0;
      } catch {
        failureCount += 1;
        if (failureCount >= 3 && ['排队中', '分析中', '提交中'].includes(agentStatusRef.current)) {
          setAgentStatus('状态读取失败');
          agentStatusRef.current = '状态读取失败';
          setMessage('Agent仍可能在后台运行，但页面连续读取状态失败，请检查本地服务连接');
        }
      } finally {
        requestRunning = false;
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 1800);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeSection, active?.job?.id, active?.job?.source_type]);

  async function loadJob(id, nextSection = 'review') {
    const res = await fetch(`${API}/api/jobs/${id}`);
    const data = await res.json();
    setActive(data);
    if (data.images?.length) {
      const batchReferenceId = nextSection === 'calibration' ? data.batch_calibration?.source_image_id : '';
      const referenceExists = batchReferenceId && data.images.some((image) => image.id === batchReferenceId);
      const stillExists = data.images.some((image) => image.id === selectedImageId);
      setSelectedImageId(referenceExists ? batchReferenceId : (stillExists ? selectedImageId : data.images[0].id));
    } else {
      setSelectedImageId('');
    }
    setSelectedDefectId('');
    setReviewPage(1);
    setAgentResult(null);
    setAgentStatus('未开始');
    if (nextSection) setActiveSection(nextSection);
    await loadJobs();
  }

  function switchSection(id) {
    if (id === 'calibration' && active?.batch_calibration?.source_image_id) {
      setSelectedImageId(active.batch_calibration.source_image_id);
    }
    setActiveSection(id);
  }

  async function loadCapabilities() {
    try {
      const response = await fetch(`${API}/api/app/capabilities`);
      if (response.ok) setCapabilities(await response.json());
    } catch {
      setCapabilities(null);
    }
  }

  async function loadAppSettings() {
    try {
      const response = await fetch(`${API}/api/app/settings`);
      if (!response.ok) return;
      const data = await response.json();
      setAppSettings((current) => ({ ...current, ...data, openai_api_key: '' }));
    } catch {
      // 普通浏览器开发模式下设置接口暂不可用时不阻断主流程。
    }
  }

  function updateAppSetting(key, value) {
    setAppSettings((current) => ({ ...current, [key]: value }));
  }

  async function saveAppSettings() {
    setBusy(true);
    setActionName('保存设置');
    try {
      const response = await fetch(`${API}/api/app/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(appSettings)
      });
      if (!response.ok) throw new Error(await readApiError(response));
      const data = await response.json();
      setMessage(data.message || '设置已保存');
      await loadCapabilities();
    } catch (error) {
      setMessage(`设置保存失败：${error.message}`);
    } finally {
      setBusy(false);
      setActionName('');
    }
  }

  async function testAppCapability(kind) {
    setBusy(true);
    setActionName(kind === 'agent' ? '测试Agent' : '检查成像');
    try {
      const response = await fetch(`${API}/api/app/settings/test-${kind}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(appSettings)
      });
      const data = await response.json();
      if (!response.ok || data.ok === false) throw new Error(data.detail || data.message || '当前能力不可用');
      setMessage(data.message || '检查通过');
      await loadCapabilities();
    } catch (error) {
      setMessage(`检查失败：${error.message}`);
    } finally {
      setBusy(false);
      setActionName('');
    }
  }

  function chooseRealtimeFolder() {
    if (!desktopBridge) {
      setMessage('浏览器开发模式不能直接选择本机文件夹，请填写路径；桌面版可一键选择。');
      return;
    }
    desktopBridge.selectFolder((path) => {
      if (path) {
        setRealtimePath(path);
        setMessage(`已选择数据目录：${path}`);
      }
    });
  }

  function chooseRealtimeFmcFile() {
    if (!desktopBridge) {
      setMessage('浏览器开发模式不能直接选择本机 FMC 文件，请填写完整文件路径。');
      return;
    }
    desktopBridge.selectFmcFile((path) => {
      if (path) {
        setRealtimePath(path);
        setMessage(`已选择 FMC 文件：${path}`);
      }
    });
  }

  async function exportDefectsCsv() {
    if (!active?.job?.id) return;
    let destinationPath = '';
    if (desktopBridge) {
      const suggestedName = `${active.job.batch_name || 'PAUT检测'}_缺陷明细.csv`;
      destinationPath = await new Promise((resolve) => desktopBridge.selectCsvSavePath(suggestedName, resolve));
      if (!destinationPath) {
        setMessage('已取消导出明细');
        return;
      }
    }
    setBusy(true);
    setActionName('明细导出');
    setMessage('正在导出缺陷明细...');
    try {
      const res = await fetch(`${API}/api/jobs/${active.job.id}/export/defects-csv`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(destinationPath ? { destination_path: destinationPath } : {})
      });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = await res.json();
      if (desktopBridge && data.csv_path) {
        desktopBridge.openPath(data.csv_path, (opened) => {
          setMessage(opened ? `缺陷明细已保存并打开：${data.csv_path}` : `缺陷明细已保存：${data.csv_path}`);
        });
      } else if (data.csv_url) {
        window.open(`${API}${data.csv_url}`, '_blank', 'noopener,noreferrer');
        setMessage('缺陷明细已生成，请在浏览器下载提示中选择保存位置');
      }
    } catch (error) {
      setMessage(`导出失败：${error.message}`);
    } finally {
      setActionName('');
      setBusy(false);
    }
  }

  async function generateArchive() {
    if (!active?.job?.id) return;
    let destinationPath = '';
    if (desktopBridge) {
      const suggestedName = `${active.job.batch_name || 'PAUT检测'}_检测归档.zip`;
      destinationPath = await new Promise((resolve) => desktopBridge.selectArchiveSavePath(suggestedName, resolve));
      if (!destinationPath) {
        setMessage('已取消生成归档');
        return;
      }
    }
    setBusy(true);
    setActionName('归档生成');
    setMessage('正在生成归档包...');
    try {
      const res = await fetch(`${API}/api/jobs/${active.job.id}/archive`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(destinationPath ? { destination_path: destinationPath } : {})
      });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = await res.json();
      if (desktopBridge && data.archive_path) {
        desktopBridge.openPath(data.archive_path, (opened) => {
          setMessage(opened ? `归档包已保存并打开：${data.archive_path}` : `归档包已保存：${data.archive_path}`);
        });
      } else if (data.archive_url) {
        window.open(`${API}${data.archive_url}`, '_blank', 'noopener,noreferrer');
        setMessage('归档包已生成，请在浏览器下载提示中选择保存位置');
      }
    } catch (error) {
      setMessage(`归档失败：${error.message}`);
    } finally {
      setActionName('');
      setBusy(false);
    }
  }

  async function runAgentAnalysis() {
    if (!active?.job?.id) {
      setMessage('请先选择或上传一个检测批次');
      return;
    }
    setBusy(true);
    setActionName('Agent分析');
    setAgentResult(null);
    setAgentStatus('提交中');
    agentStatusRef.current = '提交中';
    setMessage('Agent分析任务正在提交，提交后会自动跟踪进度...');
    setActiveSection('report');
    try {
      const res = await fetch(`${API}/api/jobs/${active.job.id}/agent/analyze`, { method: 'POST' });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      applyAgentResult(data, true);
    } catch (error) {
      setAgentStatus('分析失败');
      setMessage(`Agent 分析失败：${error.message}`);
    } finally {
      setActionName('');
      setBusy(false);
    }
  }

  function applyAgentResult(data, notify = false) {
    setAgentResult(data);
    if (['排队中', '分析中', '提交中'].includes(data.status)) {
      setAgentStatus('分析中');
      agentStatusRef.current = '分析中';
      if (notify) setMessage('实时多帧证据正在后台分析，页面会自动刷新结果');
    } else if (data.status === '需要重新分析') {
      setAgentStatus('需要重新分析');
      agentStatusRef.current = '需要重新分析';
      if (notify) setMessage(displaySummary(data.summary, '检测记录已更新，请重新开始Agent分析'));
    } else if (String(data.status || '').includes('失败')) {
      setAgentStatus(data.configured ? '大模型调用失败' : '分析失败');
      agentStatusRef.current = data.configured ? '大模型调用失败' : '分析失败';
      if (notify) setMessage(displaySummary(data.summary, 'Agent 分析失败，请检查配置后重试'));
    } else {
      setAgentStatus(data.configured ? '分析完成' : '本地摘要可用');
      agentStatusRef.current = data.configured ? '分析完成' : '本地摘要可用';
      if (notify) setMessage(data.configured ? 'Agent 分析完成' : 'Agent未配置，本地规则摘要仍可使用');
    }
  }

  async function updateDefect(id, patch) {
    const res = await fetch(`${API}/api/defects/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch)
    });
    if (!res.ok) throw new Error(await res.text());
    setAgentResult(null);
    setAgentStatus('需要重新分析');
    await loadJob(active.job.id, 'review');
  }

  async function batchUpdateVisibleDefects(status) {
    if (!filteredDefects.length) return;
    const confirmed = window.confirm(`确认将当前筛选出的 ${filteredDefects.length} 条缺陷标记为“${status}”？`);
    if (!confirmed) return;
    setBusy(true);
    setActionName(status === '已确认' ? '批量确认' : '批量标记误检');
    setMessage(`正在批量标记为${status}...`);
    try {
      const response = await fetch(`${API}/api/jobs/${active.job.id}/defects/review`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          defect_ids: filteredDefects.map((defect) => defect.id),
          review_status: status
        })
      });
      if (!response.ok) throw new Error(await response.text());
      setMessage(`已将当前筛选结果标记为${status}`);
      await loadJob(active.job.id, 'review');
    } catch (error) {
      setMessage(`批量操作失败：${error.message}`);
    } finally {
      setActionName('');
      setBusy(false);
    }
  }

  function buildCalibrationPayload() {
    if (!selectedImage || !active?.job?.id) return;
    let mmPerPixel = calibration.mm_per_pixel === '' ? null : Number(calibration.mm_per_pixel);
    if (calibration.calibration_method === '未标定') {
      mmPerPixel = null;
    }
    if (calibration.calibration_method === '两点比例尺') {
      const ax = Number(calibration.point_a_x);
      const ay = Number(calibration.point_a_y);
      const bx = Number(calibration.point_b_x);
      const by = Number(calibration.point_b_y);
      const distanceMm = Number(calibration.scale_distance_mm);
      const pixelDistance = Math.hypot(bx - ax, by - ay);
      if (!pixelDistance || !distanceMm || distanceMm <= 0) {
        setMessage('两点比例尺信息不完整：请在图上选两个点，并填写实际距离/mm');
        return;
      }
      mmPerPixel = distanceMm / pixelDistance;
    }
    const payload = {
      source_image_id: selectedImage.id,
      scan_area: calibration.scan_area,
      probe_or_channel: calibration.probe_or_channel,
      image_note: calibration.image_note,
      mm_per_pixel: Number.isFinite(mmPerPixel) ? mmPerPixel : null,
      calibration_method: calibration.calibration_method,
      point_a_x: calibration.point_a_x === '' ? null : Number(calibration.point_a_x),
      point_a_y: calibration.point_a_y === '' ? null : Number(calibration.point_a_y),
      point_b_x: calibration.point_b_x === '' ? null : Number(calibration.point_b_x),
      point_b_y: calibration.point_b_y === '' ? null : Number(calibration.point_b_y),
      scale_distance_mm: calibration.scale_distance_mm === '' ? null : Number(calibration.scale_distance_mm),
      calibration_note: calibration.calibration_note
    };
    return payload;
  }

  async function saveCalibration() {
    const payload = buildCalibrationPayload();
    if (!payload) return;
    setBusy(true);
    setActionName('整批标定');
    setMessage(`正在保存并应用到本批次 ${active?.images?.length || 0} 张图片...`);
    try {
      const res = await fetch(`${API}/api/jobs/${active.job.id}/calibration`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const errorText = await res.text();
        setMessage(`整批标定保存失败：${errorText}`);
        return;
      }
      const result = await res.json();
      const suffix = result.warning ? ` ${result.warning}` : '';
      setMessage(`批次标定已保存，已同步 ${result.updated_images} 张图片并更新物理坐标。${suffix}`);
      await loadJob(active.job.id, 'calibration');
      setAgentResult(null);
      setAgentStatus('需要重新分析');
    } catch (error) {
      setMessage(`整批标定保存失败：${error.message}`);
    } finally {
      setBusy(false);
      setActionName('');
    }
  }

  function pickCalibrationPoint(event) {
    if (calibration.calibration_method !== '两点比例尺' || !calibration.pickingPoint || !calibrationImageRef.current || !selectedImage) {
      return;
    }
    const point = imagePixelFromEvent(event, calibrationImageRef.current, selectedImage.width, selectedImage.height, false);
    if (!point) {
      setMessage('请点击图片有效区域进行标定取点');
      return;
    }
    const patch = calibration.pickingPoint === 'A'
      ? { point_a_x: point.x.toFixed(2), point_a_y: point.y.toFixed(2), pickingPoint: 'B' }
      : { point_b_x: point.x.toFixed(2), point_b_y: point.y.toFixed(2), pickingPoint: '' };
    setCalibration({ ...calibration, ...patch });
  }

  function imagePointFromEvent(event) {
    if (!reviewImageRef.current || !selectedImage) return null;
    const point = imagePixelFromEvent(event, reviewImageRef.current, selectedImage.width, selectedImage.height, true);
    if (!point) return null;
    const wrapRect = event.currentTarget.getBoundingClientRect();
    return {
      ...point,
      viewX: event.clientX - wrapRect.left,
      viewY: event.clientY - wrapRect.top,
    };
  }

  function startManualBox(event) {
    if (!manualDrawEnabled || !selectedImage) return;
    const point = imagePointFromEvent(event);
    if (!point) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setSelectedDefectId('');
    setIsDrawingBox(true);
    setDraftBox({
      x_min: point.x,
      y_min: point.y,
      x_max: point.x,
      y_max: point.y,
      view_x_min: point.viewX,
      view_y_min: point.viewY,
      view_x_max: point.viewX,
      view_y_max: point.viewY,
    });
    setMessage('正在框选缺陷区域，松开鼠标后会自动填入像素坐标');
  }

  function updateManualBox(event) {
    if (!isDrawingBox || !draftBox) return;
    const point = imagePointFromEvent(event);
    if (!point) return;
    setDraftBox({
      ...draftBox,
      x_max: point.x,
      y_max: point.y,
      view_x_max: point.viewX,
      view_y_max: point.viewY,
    });
  }

  function finishManualBox(event) {
    if (!isDrawingBox || !draftBox) return;
    const endPoint = event ? imagePointFromEvent(event) : null;
    const completedBox = endPoint ? {
      ...draftBox,
      x_max: endPoint.x,
      y_max: endPoint.y,
      view_x_max: endPoint.viewX,
      view_y_max: endPoint.viewY,
    } : draftBox;
    if (event?.currentTarget?.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setIsDrawingBox(false);
    const xMin = Math.min(completedBox.x_min, completedBox.x_max);
    const yMin = Math.min(completedBox.y_min, completedBox.y_max);
    const xMax = Math.max(completedBox.x_min, completedBox.x_max);
    const yMax = Math.max(completedBox.y_min, completedBox.y_max);
    if (xMax - xMin < 3 || yMax - yMin < 3) {
      setDraftBox(null);
      setMessage('框选区域太小，请重新拖拽一个完整的缺陷外接矩形');
      return;
    }
    setDraftBox({
      x_min: xMin,
      y_min: yMin,
      x_max: xMax,
      y_max: yMax,
      view_x_min: Math.min(completedBox.view_x_min, completedBox.view_x_max),
      view_y_min: Math.min(completedBox.view_y_min, completedBox.view_y_max),
      view_x_max: Math.max(completedBox.view_x_min, completedBox.view_x_max),
      view_y_max: Math.max(completedBox.view_y_min, completedBox.view_y_max),
    });
    setDefectForm({
      ...defectForm,
      x_min: xMin.toFixed(2),
      y_min: yMin.toFixed(2),
      x_max: xMax.toFixed(2),
      y_max: yMax.toFixed(2)
    });
    setManualDrawEnabled(false);
    setMessage('已从图片框选缺陷区域，请确认类型和备注后点击“补录”保存');
  }

  async function createManualDefect() {
    if (!selectedImage || !active?.job?.id) return;
    const coords = {
      x_min: Number(defectForm.x_min),
      y_min: Number(defectForm.y_min),
      x_max: Number(defectForm.x_max),
      y_max: Number(defectForm.y_max)
    };
    if (!Object.values(coords).every(Number.isFinite) || coords.x_max <= coords.x_min || coords.y_max <= coords.y_min) {
      setMessage('请先在图片上框选缺陷，或填写有效的左/上/右/下边界像素');
      return;
    }
    if (coords.x_min < 0 || coords.y_min < 0 || coords.x_max > selectedImage.width || coords.y_max > selectedImage.height) {
      setMessage('缺陷框超出当前图片范围，请重新框选或修正坐标');
      return;
    }
    const payload = {
      class_name: defectForm.class_name || '人工标注缺陷',
      confidence: 1,
      ...coords,
      review_status: '已确认',
      suggestion: defectForm.suggestion,
      remark: defectForm.remark
    };
    setBusy(true);
    setActionName('保存补录');
    setMessage('正在保存人工补录...');
    try {
      const res = await fetch(`${API}/api/images/${selectedImage.id}/defects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error(await res.text());
      setAgentResult(null);
      setAgentStatus('需要重新分析');
      setMessage('人工缺陷已补录，标注图已更新');
      setDraftBox(null);
      await loadJob(active.job.id, 'review');
    } catch (error) {
      setMessage(`人工补录失败：${error.message}`);
    } finally {
      setActionName('');
      setBusy(false);
    }
  }

  async function saveDefectEdit() {
    if (!selectedDefect || !active?.job?.id) return;
    setBusy(true);
    setActionName('保存修改');
    setMessage('正在保存缺陷修改...');
    try {
      await updateDefect(selectedDefect.id, {
        class_name: defectForm.class_name,
        x_min: Number(defectForm.x_min),
        y_min: Number(defectForm.y_min),
        x_max: Number(defectForm.x_max),
        y_max: Number(defectForm.y_max),
        suggestion: defectForm.suggestion,
        remark: defectForm.remark
      });
      setMessage('缺陷信息已保存');
    } catch (error) {
      setMessage(`保存失败：${error.message}`);
    } finally {
      setActionName('');
      setBusy(false);
    }
  }

  async function markSelectedDefect(status) {
    if (!selectedDefect || !active?.job?.id) return;
    setBusy(true);
    setActionName(status === '已确认' ? '确认缺陷' : '标记误检');
    setMessage(`正在将当前缺陷标记为${status}...`);
    try {
      await updateDefect(selectedDefect.id, { review_status: status });
      setMessage(status === '已确认' ? '已确认当前缺陷' : '已标记当前缺陷为误检');
    } catch (error) {
      setMessage(`操作失败：${error.message}`);
    } finally {
      setActionName('');
      setBusy(false);
    }
  }

  async function deleteSelectedDefect() {
    if (!selectedDefect || !active?.job?.id) return;
    if (!window.confirm('确认删除当前选中的缺陷记录？')) return;
    setBusy(true);
    setActionName('删除缺陷');
    const res = await fetch(`${API}/api/defects/${selectedDefect.id}`, { method: 'DELETE' });
    if (!res.ok) {
      setMessage(`删除失败：${await res.text()}`);
      setActionName('');
      setBusy(false);
      return;
    }
    setAgentResult(null);
    setAgentStatus('需要重新分析');
    setSelectedDefectId('');
    setMessage('缺陷已删除，标注图已更新');
    await loadJob(active.job.id, 'review');
    setActionName('');
    setBusy(false);
  }

  const defectCount = active?.defects?.length || 0;
  const imageCount = active?.images?.length || 0;
  const reviewedCount = active?.defects?.filter((d) => d.review_status === '已确认').length || 0;
  const pendingCount = active?.defects?.filter((d) => d.review_status === '待复核').length || 0;
  const falsePositiveCount = active?.defects?.filter((d) => d.review_status === '误检').length || 0;
  const defectsWithImage = useMemo(() => {
    const imageById = Object.fromEntries((active?.images || []).map((image) => [image.id, image]));
    return (active?.defects || []).map((defect) => ({ ...defect, image: imageById[defect.image_id] }));
  }, [active]);
  const filteredDefects = useMemo(() => {
    const query = reviewQuery.trim().toLowerCase();
    return defectsWithImage.filter((defect) => {
      const statusOk = reviewFilter === '全部' || defect.review_status === reviewFilter;
      const queryOk = !query || [
        defect.class_name,
        defect.review_status,
        defect.image?.original_name,
        defect.image?.scan_area,
        defect.image?.area_label,
        defect.suggestion,
        defect.remark
      ].some((value) => String(value || '').toLowerCase().includes(query));
      return statusOk && queryOk;
    });
  }, [defectsWithImage, reviewFilter, reviewQuery]);
  const reviewPageSize = 20;
  const reviewTotalPages = Math.max(1, Math.ceil(filteredDefects.length / reviewPageSize));
  const currentReviewPage = Math.min(reviewPage, reviewTotalPages);
  const pagedDefects = filteredDefects.slice((currentReviewPage - 1) * reviewPageSize, currentReviewPage * reviewPageSize);
  const visibleDraftBox = draftBox && selectedImage ? {
    left: `${Math.min(draftBox.view_x_min, draftBox.view_x_max)}px`,
    top: `${Math.min(draftBox.view_y_min, draftBox.view_y_max)}px`,
    width: `${Math.abs(draftBox.view_x_max - draftBox.view_x_min)}px`,
    height: `${Math.abs(draftBox.view_y_max - draftBox.view_y_min)}px`
  } : null;
  const selectedImageDefects = selectedImage
    ? defectsWithImage.filter((defect) => defect.image_id === selectedImage.id)
    : [];

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brandMark">ND</div>
          <div>
            <strong>缺陷智能检测</strong>
            <span>Inspection Console</span>
          </div>
        </div>
        <nav>
          <button className={`navItem ${activeSection === 'realtime' ? 'active' : ''}`} onClick={() => switchSection('realtime')}><Activity size={18} />实时监控</button>
          <button disabled={!active} className={`navItem ${activeSection === 'calibration' ? 'active' : ''}`} onClick={() => switchSection('calibration')}><Ruler size={18} />坐标标定</button>
          <button disabled={!active} className={`navItem ${activeSection === 'review' ? 'active' : ''}`} onClick={() => switchSection('review')}><CheckCircle2 size={18} />结果复核</button>
          <button disabled={!active} className={`navItem ${activeSection === 'report' ? 'active' : ''}`} onClick={() => switchSection('report')}><FileText size={18} />报告与Agent</button>
          <button className={`navItem ${activeSection === 'settings' ? 'active' : ''}`} onClick={() => switchSection('settings')}><Settings2 size={18} />应用设置</button>
        </nav>
        <section className="jobList">
          <div className="sectionTitle">
            <span>历史批次</span>
            <button onClick={loadJobs} title="刷新"><RefreshCw size={16} /></button>
          </div>
          {jobs.filter((job) => (
            job.id !== realtimeStatus?.target_job_id || realtimeStatus?.handoff_job_id === job.id
          )).map((job) => (
            <button key={job.id} className={`jobItem ${active?.job?.id === job.id ? 'active' : ''}`} onClick={() => loadJob(job.id)}>
              <strong>{job.batch_name}</strong>
              <span>{job.status}{job.storage_state === 'compacted' ? ' · 已压缩' : ''} · {job.created_at}</span>
            </button>
          ))}
        </section>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>PAUT 缺陷智能检测系统</h1>
            <p>数据接入、缺陷检测、结果复核、智能分析与报告归档</p>
          </div>
          <span className={`status topStatus ${realtimeStatus?.state?.includes('异常') ? 'bad' : realtimeStatus?.running ? 'ok' : ''}`}>
            {realtimeStatus?.running ? '正在处理' : realtimeStatus?.state || '等待数据'}
          </span>
        </header>

        {message && <div className={`globalNotice ${/(失败|错误|异常)/.test(message) ? 'bad' : ''}`} role="status">
          <span>{message}</span>
          <button onClick={() => setMessage('')} title="关闭提示"><X size={16} /></button>
        </div>}

        {activeSection === 'realtime' && <section className="pageStack realtimePage">
          <section className="panel">
            <div className="panelHeader">
              <h2><Activity size={19} />数据源与处理</h2>
              <span className={`status ${realtimeStatus?.state?.includes('异常') ? 'bad' : realtimeStatus?.running ? 'ok' : ''}`}>
                {realtimeStatus?.state || '未配置'}
              </span>
            </div>
            <div className="workflowHint">
              <strong>操作顺序</strong>
              <span>选择 A扫目录或单个 FMC 文件 → 校验数据源 → 开始处理 → 停止后自动归档并进入复核 → Agent与报告</span>
              {cacheStatus && <span>缓存维护：保留 {cacheStatus.retention_days} 天，每 {cacheStatus.cleanup_interval_hours} 小时自动检查；历史复核记录和报告不删除。</span>}
            </div>
            <div className="realtimeControls">
              <label className="field realtimePathField">
                <span>A扫目录或 FMC 文件</span>
                <input value={realtimePath} onChange={(event) => setRealtimePath(event.target.value)} placeholder="目录或单个 FMC 文件，如 C:\...\1.txt" />
              </label>
              <button className="ghost" disabled={realtimeBusy || realtimeStatus?.running || realtimeStatus?.finalizing} onClick={chooseRealtimeFolder}><FolderOpen size={17} />选择目录</button>
              <button className="ghost" disabled={realtimeBusy || realtimeStatus?.running || realtimeStatus?.finalizing} onClick={chooseRealtimeFmcFile}><FileText size={17} />选择 FMC 文件</button>
              <label className="field realtimeIntervalField">
                <span>{simulationEnabled ? '扫查间隔（秒）' : '处理间隔（秒）'}</span>
                <input type="number" min="0" step="0.1" value={realtimeInterval} onChange={(event) => setRealtimeInterval(event.target.value)} placeholder="0" />
              </label>
              <label className="field"><span>模拟车辆扫查</span><input type="checkbox" checked={simulationEnabled} onChange={(e) => { setSimulationEnabled(e.target.checked); if (e.target.checked && !Number(realtimeInterval)) setRealtimeInterval(5); }} disabled={realtimeStatus?.running} /></label>
              {simulationEnabled && <label className="field"><span>模拟车速（mm/s，0为静止）</span><input type="number" min="0" max="10000" value={vehicleSpeed} onChange={(e) => setVehicleSpeed(e.target.value)} /></label>}
              {realtimeStatus?.received_count != null && <span>已扫查 {realtimeStatus.received_count} · 等待处理 {realtimeStatus.waiting_count || 0} · {realtimeStatus.reception_state}</span>}
              <button className="ghost" disabled={realtimeBusy || realtimeStatus?.running || realtimeStatus?.finalizing || !realtimePath.trim()} onClick={configureRealtime}><CheckCircle2 size={17} />校验数据源</button>
              <button className="primary" disabled={realtimeBusy || realtimeStatus?.running || realtimeStatus?.finalizing || validatedSource !== realtimePath.trim() || !validatedSource || realtimeStatus?.handoff_job_id || realtimeStatus?.state === '处理已中断'} onClick={() => controlRealtime('start')}><Play size={17} />开始处理</button>
              <button className="danger" disabled={realtimeBusy || realtimeStatus?.finalizing || !realtimeStatus?.running} onClick={() => controlRealtime('stop')}>停止处理</button>
            </div>

            <details className="optionalSource">
              <summary>没有本地目录？导入 PAUT ZIP 数据包</summary>
              <div className="zipImportRow">
                <label className="zipFilePicker">
                  <FolderOpen size={18} />
                  <span>{pautZipFile ? pautZipFile.name : '选择包含 A扫数据的 .zip 文件'}</span>
                  <input type="file" accept=".zip" onChange={handlePautZipSelect} />
                </label>
                <button className="ghost" disabled={!pautZipFile || pautBusy || realtimeStatus?.running} onClick={uploadPautZip}>
                  <UploadCloud size={17} />{pautBusy ? '正在导入...' : '导入并载入'}
                </button>
              </div>
            </details>

            <details className="optionalSource tofdImport">
              <summary>独立 TOFD 数据入口</summary>
              <div className="zipImportRow">
                <label className="zipFilePicker">
                  <FolderOpen size={18} />
                  <span>{tofdZipFile ? tofdZipFile.name : '选择 Tx1→Rx128 紧凑 MAT（推荐）或原始 ZIP'}</span>
                  <input type="file" accept=".mat,.zip" onChange={handleTofdZipSelect} />
                </label>
                <button className="ghost" disabled={!tofdZipFile || tofdBusy || realtimeStatus?.running} onClick={uploadAndAnalyzeTofd}>
                  <UploadCloud size={17} />{tofdBusy ? '正在分析...' : '导入并分析 TOFD'}
                </button>
              </div>
              <p className="sourceSeparationNote">推荐使用已校验的 Tx1→Rx128 紧凑 MAT；也兼容原始 Save_* 二进制 ZIP。TOFD 与 PAUT 成像相互独立。</p>

              <div className="zipImportRow">
                <label className="field" style={{ flex: 1 }}>
                  <span>本地紧凑 MAT 文件或原始数据文件夹</span>
                  <input type="text" placeholder="例如 C:\...\tx1_rx128_raw_20260831.mat" value={tofdLocalDir} onChange={(e) => setTofdLocalDir(e.target.value)} />
                </label>
                <button className="ghost" disabled={!tofdLocalDir.trim() || tofdBusy || realtimeStatus?.running} onClick={analyzeTofdFromLocalDir}>
                  <Play size={17} />{tofdBusy ? '正在分析...' : '直接分析本地数据'}
                </button>
              </div>

              <div className="imagingParamsGrid">
                <label className="field">
                  <span>材料声速 (m/s)</span>
                  <input type="number" value={tofdParams.velocity_mps} onChange={(e) => updateTofdParam('velocity_mps', e.target.value)} />
                </label>
                <label className="field">
                  <span>板厚 (mm)</span>
                  <input type="number" step="0.1" value={tofdParams.plate_thickness_mm} onChange={(e) => updateTofdParam('plate_thickness_mm', e.target.value)} />
                </label>
                <label className="field">
                  <span>PCS 探头中心距 (mm)</span>
                  <input type="number" step="0.1" value={tofdParams.tofd_pcs_mm} onChange={(e) => updateTofdParam('tofd_pcs_mm', e.target.value)} />
                </label>
                <div className="field">
                  <span>紧凑数据通道</span>
                  <strong>Tx1 → Rx128（由文件元数据校验）</strong>
                </div>
              </div>
            </details>

            {tofdResult?.image_url && <section className="tofdStandaloneResult">
              <div className="tofdStandaloneImage">
                <img src={`${API}${tofdResult.image_url}`} alt="TOFD 实采数据成像与分析" />
              </div>
              <div className="detailRows">
                <p><span>扫描位置</span><strong>{tofdResult.analysis?.scan_count ?? '-'}</strong></p>
                <p><span>有效首波</span><strong>{tofdResult.analysis ? `${tofdResult.analysis.valid_first_arrivals}/${tofdResult.analysis.scan_count}` : '-'}</strong></p>
                <p><span>探头通道</span><strong>{tofdResult.analysis ? `Tx${tofdResult.analysis.tx_channel} → Rx${tofdResult.analysis.rx_channel}` : '-'}</strong></p>
                <p><span>PCS / 板厚</span><strong>{tofdResult.analysis ? `${Number(tofdResult.analysis.pcs_mm).toFixed(1)} / ${Number(tofdResult.analysis.plate_thickness_mm).toFixed(1)} mm` : '-'}</strong></p>
                <p><span>理论侧向波</span><strong>{tofdResult.analysis ? `${Number(tofdResult.analysis.expected_lateral_wave_us).toFixed(3)} μs` : '-'}</strong></p>
                <p><span>理论底面波</span><strong>{tofdResult.analysis ? `${Number(tofdResult.analysis.expected_backwall_us).toFixed(3)} μs` : '-'}</strong></p>
              </div>
            </section>}

            <div className="imagingParamsToggle">
              <button className="ghost compact" onClick={() => setShowImagingParams((v) => !v)}>
                {showImagingParams ? '收起' : '展开'}成像参数
              </button>
              <span>用于本次检测的成像参数</span>
            </div>

            {showImagingParams && <div className="imagingParamsGrid">
              <label className="field">
                <span>材料声速 (m/s)</span>
                <input type="number" value={imagingParams.velocity_mps} onChange={(e) => updateImagingParam('velocity_mps', e.target.value)} />
              </label>
              <label className="field">
                <span>采样率 (MHz)</span>
                <input type="number" min="0.001" step="0.1" value={imagingParams.sample_rate_mhz} onChange={(e) => updateImagingParam('sample_rate_mhz', e.target.value)} />
              </label>
              <label className="field">
                <span>板厚 (mm)</span>
                <input type="number" value={imagingParams.plate_thickness_mm} onChange={(e) => updateImagingParam('plate_thickness_mm', e.target.value)} />
              </label>
              <label className="field">
                <span>横向起点 x_min (mm)</span>
                <input type="number" step="0.1" value={imagingParams.x_min_mm} onChange={(e) => updateImagingParam('x_min_mm', e.target.value)} />
              </label>
              <label className="field">
                <span>横向终点 x_max (mm)</span>
                <input type="number" step="0.1" value={imagingParams.x_max_mm} onChange={(e) => updateImagingParam('x_max_mm', e.target.value)} />
              </label>
              <label className="field">
                <span>深度起点 z_min (mm)</span>
                <input type="number" step="0.1" value={imagingParams.z_min_mm} onChange={(e) => updateImagingParam('z_min_mm', e.target.value)} />
              </label>
              <label className="field">
                <span>深度终点 z_max (mm)</span>
                <input type="number" step="0.1" value={imagingParams.z_max_mm} onChange={(e) => updateImagingParam('z_max_mm', e.target.value)} />
              </label>
              <label className="field">
                <span>像素步长 (mm)</span>
                <input type="number" step="0.05" value={imagingParams.pixel_step_mm} onChange={(e) => updateImagingParam('pixel_step_mm', e.target.value)} />
              </label>
              <label className="field">
                <span>动态范围 (dB)</span>
                <input type="number" step="1" value={imagingParams.dynamic_range_db} onChange={(e) => updateImagingParam('dynamic_range_db', e.target.value)} />
              </label>
              <label className="field">
                <span>高斯平滑 σ (mm)</span>
                <input type="number" step="0.1" value={imagingParams.gaussian_smoothing_sigma_mm} onChange={(e) => updateImagingParam('gaussian_smoothing_sigma_mm', e.target.value)} />
              </label>
              <label className="field probePositionsField">
                <span>探头识别</span>
                <input value="自动识别阵元坐标并用于后续计算" readOnly />
              </label>
            </div>}
          </section>

          <section className="realtimeMetrics">
            <Metric label="处理状态" value={realtimeStatus?.message || '等待配置'} />
            <Metric label="采集进度" value={realtimeStatus?.source_mode === '序列目录' ? `${realtimeStatus?.processed_source_groups || 0}/${realtimeStatus?.source_group_count || 0} 组` : `${realtimeStatus?.frame_count ?? 0} 帧`} />
            <Metric label="数据状态" value={realtimeStatus?.sequence_complete ? '序列处理完成' : realtimeStatus?.source_changed === false ? (realtimeStatus?.source_mode === '序列目录' ? '等待新增数据组' : '等待设备写入') : realtimeStatus?.source_changed ? '已扫查新数据' : '-'} />
            <Metric label="处理耗时" value={realtimeStatus?.last_processing_ms ? `${realtimeStatus.last_processing_ms} ms` : '-'} />
          </section>

          {realtimeStatus?.geometry_status && realtimeStatus.geometry_status !== 'unknown' && <div className={`geometryNotice ${realtimeStatus.geometry_status === 'assumed' ? 'warn' : ''}`}>
            <strong>阵元配置：{realtimeStatus.geometry_status === 'assumed' ? '暂定假设' : '已提供或已确认'}</strong>
            <span>{realtimeStatus.geometry_note || '阵元坐标已通过数据源校验。'}</span>
          </div>}

          {['通过', '部分通过'].includes(realtimeStatus?.input_validation?.status) && <div className={`geometryNotice ${realtimeStatus.input_validation.status === '部分通过' ? 'warn' : ''}`}>
            <strong>数据校验：{realtimeStatus.input_validation.status}</strong>
            <span>
              发现 {realtimeStatus.input_validation.discovered_count ?? realtimeStatus.input_validation.group_count ?? 0} 组，
              通过 {realtimeStatus.input_validation.valid_count ?? realtimeStatus.input_validation.group_count ?? 0} 组，
              跳过 {realtimeStatus.input_validation.skipped_count ?? 0} 组；
              探头配置 {Array.isArray(realtimeStatus.input_validation.tx_positions_mm)
                ? `${realtimeStatus.input_validation.tx_positions_mm[0]}–${realtimeStatus.input_validation.tx_positions_mm.at(-1)} mm`
                : realtimeStatus.input_validation.tx_positions_mm === 'mixed' ? '混合（逐组自动匹配）' : '待识别'}；
              {realtimeStatus.background_reference?.enabled
                ? `已使用背景参考（${realtimeStatus.background_reference.source}）`
                : '本次未使用背景参考'}
              {!!realtimeStatus.input_validation.warnings?.length && `。${realtimeStatus.input_validation.warnings.slice(0, 3).join('；')}`}
            </span>
          </div>}

          {realtimeStatus?.last_error && <section className="realtimeError" role="alert">
            <strong>{realtimeStatus.last_error.stage}发生错误</strong>
            <span>{realtimeStatus.last_error.message}</span>
            <time>{realtimeStatus.last_error.time}</time>
          </section>}

          <section className="panel liveModeBar">
            <div>
              <strong>{followRealtime ? '实时画面自动滚动' : '实时画面已暂停'}</strong>
              <span>{followRealtime ? '每组数据成像完成后自动显示最新结果，后台处理不依赖当前画面。' : '后台仍继续处理；恢复后立即显示最新结果。'}</span>
            </div>
            {advancedReviewEnabled
              ? <button className="ghost" disabled>高级复核中</button>
              : followRealtime
                ? <button className="ghost" onClick={pauseRealtimeFollowing}>暂停画面</button>
                : <button className="primary" onClick={resumeRealtimeFollowing}><RefreshCw size={17} />恢复实时画面</button>}
          </section>

          <section className="realtimeWorkspace">
            <section className="panel realtimeImagePanel">
              <div className="panelHeader">
                <h2><FileImage size={19} />新版 PAUT 成像结果</h2>
                <div className="realtimeImageActions">
                  <span>{realtimeFrame?.received_at ? `扫查时间：${realtimeFrame.scan_receipt ? new Date(realtimeFrame.received_at).toLocaleString('zh-CN', { hour12: false }) : realtimeFrame.received_at}` : '尚无数据'}</span>
                  <button className={pautImageView === 'thresholded' ? 'primary compact' : 'ghost compact'} disabled={!realtimeFrame?.image?.thresholded_url} onClick={() => setPautImageView('thresholded')}>增强成像（推荐）</button>
                  <button className={pautImageView === 'original' ? 'primary compact' : 'ghost compact'} disabled={!realtimeFrame?.image?.original_url} onClick={() => setPautImageView('original')}>原始成像</button>
                  <button className="ghost compact" disabled={!realtimeFrame?.image?.url} onClick={() => setRealtimeImagePreview(pautImageView === 'original' && realtimeFrame.image.original_url ? realtimeFrame.image.original_url : (realtimeFrame.image.annotated_url || realtimeFrame.image.thresholded_url || realtimeFrame.image.url))}><Maximize2 size={16} />放大</button>
                  <button className="ghost compact" disabled={!realtimeSessions.length} onClick={() => openRealtimeReplay(realtimeSessions[0])}>批次复盘</button>
                  <button className={advancedReviewEnabled ? 'primary compact' : 'ghost compact'} onClick={toggleAdvancedReview} disabled={!realtimeFrame}>高级复核</button>
                </div>
              </div>
              {realtimeFrame?.image?.url
                ? <div className={`realtimeImageWrap ${advancedReviewEnabled ? 'inspectionEnabled' : ''}`} onPointerDown={inspectRealtimePoint} role={advancedReviewEnabled ? 'button' : undefined} tabIndex={advancedReviewEnabled ? 0 : undefined} aria-label={advancedReviewEnabled ? '点击DAS图像选择高级复核位置' : undefined}>
                    <img ref={realtimeImageRef} draggable="false" src={`${API}${pautImageView === 'original' && realtimeFrame.image.original_url ? realtimeFrame.image.original_url : (realtimeFrame.image.annotated_url || realtimeFrame.image.thresholded_url || realtimeFrame.image.url)}?frame=${realtimeFrame.frame_id}`} alt={pautImageView === 'original' ? 'PAUT原始未截断成像' : 'PAUT深度补偿阈值截断成像'} />
                    {advancedReviewEnabled && realtimePickMarker && <span className="realtimePickMarker" style={{ left: realtimePickMarker.left, top: realtimePickMarker.top }} aria-hidden="true" />}
                    {advancedReviewEnabled && waveformBusy && <span className="realtimePickBusy">正在读取相关A扫...</span>}
                    {advancedReviewEnabled && <span className="imageInteractionHint">点击疑似位置查看相关A扫通道</span>}
                  </div>
                : <div className="realtimeEmpty">开始处理后，此处显示由A扫数据生成的DAS图像。</div>}
              {realtimeFrame?.image?.display_threshold_only && <p className="displayMethodNote">增强阈值只影响显示；缺陷分类和尺寸定量始终基于原始成像数据。</p>}
            </section>
            <section className="panel realtimeDetailPanel">
              <div className="panelHeader"><h2><Server size={19} />当前分析结果</h2><span>成像、分类与定量结果同组存储</span></div>
              <div className="detailRows">
                <p><span>数据组</span><strong>{realtimeFrame?.source_group || '-'}</strong></p>
                <p><span>采集数据</span><strong>{realtimeFrame?.shape?.join(' × ') || '-'}</strong></p>
                <p><span>峰值位置</span><strong>{realtimeFrame?.image?.peak_x_mm != null && realtimeFrame?.image?.peak_z_mm != null ? `x=${fmt(realtimeFrame.image.peak_x_mm)} mm，z=${fmt(realtimeFrame.image.peak_z_mm)} mm` : '未提供'}</strong></p>
                <p><span>检测结果</span><strong>{displaySummary(realtimeFrame?.analysis?.paut_v2?.summary || realtimeFrame?.analysis?.joint?.summary)}</strong></p>
                <p><span>疑似缺陷数</span><strong>{realtimeFrame?.analysis?.paut_v2 ? `${realtimeFrame.analysis.paut_v2.count} 处` : '-'}</strong></p>
                {realtimeFrame?.scan_receipt?.mode === 'simulation' && <p><span>模拟扫查距离</span><strong>{Number(realtimeFrame.scan_receipt.estimated_scan_distance_mm).toFixed(2)} mm（估算）</strong></p>}
                <p><span>结果质量</span><strong>{realtimeFrame?.analysis?.paut_v2?.quality ? `${realtimeFrame.analysis.paut_v2.quality.status || '未评价'} · ${Number(realtimeFrame.analysis.paut_v2.quality.issue_count || 0)} 条提示` : '未评价'}</strong></p>
              </div>
              {!!realtimeFrame?.defects?.length && <div className="ascanResults">
                <div className="ascanResultsHeader">缺陷分类与定量结果</div>
                {realtimeFrame.defects.map((defect, index) => (
                  <div className="ascanResultRow pautV2ResultRow" key={`${defect.defect_id || defect.X_mm}-${index}`}>
                    <strong>#{index + 1}</strong>
                    <span><b>{defect.class_name || defect.MLLevel || '待复核'}</b> · x={fmt(defect.x_mm ?? defect.X_mm)} mm · z={fmt(defect.z_mm ?? defect.Z_mm)} mm</span>
                    <span>{defect.corrected_length_mm != null ? `校正长度 ${fmt(defect.corrected_length_mm)} mm` : defect.diameter_mm != null ? `孔径 ${fmt(defect.diameter_mm)} mm${defect.area_mm2 != null ? ` · 面积 ${fmt(defect.area_mm2)} mm²` : ''}` : '二维数据未提供'} · SNR {defect.snr_db != null ? `${fmt(defect.snr_db)} dB` : '未提供'}</span>
                    {defect.warning && <small>{defect.warning}</small>}
                  </div>
                ))}
              </div>}
            </section>
          </section>
          {realtimeImagePreview && <div className="imagePreviewOverlay" role="dialog" aria-modal="true" aria-label="PAUT成像放大预览" onClick={() => setRealtimeImagePreview('')}>
            <section className="imagePreviewWindow" onClick={(event) => event.stopPropagation()}>
              <div className="imagePreviewHeader"><strong>PAUT成像完整预览</strong><button className="ghost compact" onClick={() => setRealtimeImagePreview('')}><X size={17} />关闭</button></div>
              <img src={`${API}${realtimeImagePreview}?frame=${realtimeFrame?.frame_id || ''}`} alt="PAUT成像完整预览" />
            </section>
          </div>}
          {replaySession && <div className="replayOverlay" role="dialog" aria-modal="true" aria-label="实时处理批次复盘">
            <section className="replayWindow">
              <div className="panelHeader replayHeader">
                <div>
                  <h2><FileImage size={19} />处理批次复盘</h2>
                  <span>{replaySession.session_id} · {replaySession.state} · 共 {replayFrames.length} 帧</span>
                </div>
                <button className="ghost compact" onClick={() => setReplaySession(null)}><X size={17} />关闭</button>
              </div>
              <div className="replayBody">
                <aside className="replaySessions">
                  <strong>处理批次</strong>
                  {realtimeSessions.map((session) => <button key={session.session_id} className={session.session_id === replaySession.session_id ? 'selected' : ''} onClick={() => openRealtimeReplay(session)}>
                    <span>{session.started_at || session.updated_at}</span>
                    <small>{session.state} · {session.frame_count || 0} 帧</small>
                  </button>)}
                </aside>
                <main className="replayViewer">
                  {replayFrames[replayIndex]?.image?.url ? <>
                    <div className="replayImageStage">
                      <img src={`${API}${replayFrames[replayIndex].image.annotated_url || replayFrames[replayIndex].image.url}`} alt="批次复盘DAS成像与A扫标记" />
                    </div>
                    <div className="replayCaption">
                      <strong>{replayFrames[replayIndex].source_group}</strong>
                      <span>A扫候选 {replayFrames[replayIndex].analysis?.ascan?.count || 0} 个 · 扫查时间 {replayFrames[replayIndex].received_at}</span>
                    </div>
                    <input type="range" min="0" max={Math.max(0, replayFrames.length - 1)} value={replayIndex} onChange={(event) => setReplayIndex(Number(event.target.value))} />
                    <div className="replayControls">
                      <button className="ghost" disabled={replayIndex <= 0} onClick={() => setReplayIndex((value) => value - 1)}>上一帧</button>
                      <span>{replayIndex + 1} / {replayFrames.length}</span>
                      <button className="ghost" disabled={replayIndex >= replayFrames.length - 1} onClick={() => setReplayIndex((value) => value + 1)}>下一帧</button>
                    </div>
                  </> : <div className="realtimeEmpty">这个批次尚无已完成图像。</div>}
                </main>
              </div>
            </section>
          </div>}
          {advancedReviewEnabled && (realtimeFocus || realtimeWaveform) && <section className="waveformWorkspace">
            <section className="panel channelPanel">
              <div className="panelHeader"><h2><Activity size={19} />相关A扫通道</h2><span>{realtimeFocus ? `x=${realtimeFocus.x_mm} mm / z=${realtimeFocus.z_mm} mm` : '-'}</span></div>
              <div className="channelList">
                {(realtimeFocus?.channels || []).map((channel) => (
                  <button key={`${channel.tx_index}-${channel.rx_index}`} className={realtimeWaveform?.tx_index === channel.tx_index && realtimeWaveform?.rx_index === channel.rx_index ? 'selected' : ''} onClick={() => loadRealtimeWaveform(channel.tx_index, channel.rx_index, realtimeFocus.frame_id)}>
                    <strong>{channel.tx_label} → {channel.rx_label}</strong>
                    <span>点击读取原始波形</span>
                  </button>
                ))}
              </div>
            </section>
            <section className="panel waveformPanel">
              <div className="panelHeader"><h2><Activity size={19} />A扫波形与闸门</h2><span>{waveformBusy ? '读取中...' : realtimeWaveform ? `${realtimeWaveform.tx_label} → ${realtimeWaveform.rx_label}` : '-'}</span></div>
              {realtimeWaveform ? <WaveformChart data={realtimeWaveform} /> : <div className="emptyRow">请在DAS图像上选择反查点。</div>}
            </section>
          </section>}
        </section>}

        {activeSection === 'calibration' && <section className="panel calibrationPanel">
          <div className="panelHeader">
            <h2><Ruler size={19} />坐标标定</h2>
            <div className="realtimeImageActions">
              <span>{active?.batch_calibration?.mm_per_pixel ? '本批次已标定，可重新保存修改' : '本批次未标定，仅输出像素坐标'}</span>
              <select aria-label="选择标定参考图" value={selectedImage?.id || ''} onChange={(e) => setSelectedImageId(e.target.value)}>
                {(active?.images || []).map((image) => <option key={image.id} value={image.id}>{image.original_name}</option>)}
              </select>
            </div>
          </div>
          <div className="calibrationHint">
            <strong>整批标定</strong>
            <p>选择一张参考图完成标定，保存后自动应用到本批次全部 {active?.images?.length || 0} 张图片并写入批次记录。重新标定并保存即可覆盖修改。坐标原点为图片左上角，x 向右，y 向下。</p>
          </div>
          <div className="calibrationWorkbench">
            <div className="imageStage calibrationStage">
              {selectedImage ? (
                <div className="calibrationImageWrap">
                  <img ref={calibrationImageRef} src={`${API}${selectedImage.url}`} alt="标定图片" onClick={pickCalibrationPoint} />
                  {calibration.point_a_x && <span className="calibrationPoint pointA" style={{ left: `${(Number(calibration.point_a_x) / selectedImage.width) * 100}%`, top: `${(Number(calibration.point_a_y) / selectedImage.height) * 100}%` }}>A</span>}
                  {calibration.point_b_x && <span className="calibrationPoint pointB" style={{ left: `${(Number(calibration.point_b_x) / selectedImage.width) * 100}%`, top: `${(Number(calibration.point_b_y) / selectedImage.height) * 100}%` }}>B</span>}
                </div>
              ) : (
                <div className="empty"><Search size={36} />请选择或上传一个检测批次</div>
              )}
            </div>
            <div className="calibrationControls">
              <div className="segmented">
                {['未标定', '手动比例', '两点比例尺'].map((item) => (
                  <button key={item} className={calibration.calibration_method === item ? 'active' : ''} onClick={() => setCalibration({ ...calibration, calibration_method: item })}>{item}</button>
                ))}
              </div>
              <div className="calibrationGrid simpleCalibrationGrid">
                <Input label="扫查区域" placeholder="请输入区域，如：焊缝A区/第1段" value={calibration.scan_area} onChange={(v) => setCalibration({ ...calibration, scan_area: v })} />
                <Input label="探头/通道" placeholder="请输入设备通道，如：PAUT通道1" value={calibration.probe_or_channel} onChange={(v) => setCalibration({ ...calibration, probe_or_channel: v })} />
                <Input label="图片备注" placeholder="请输入图片说明，可留空" value={calibration.image_note} onChange={(v) => setCalibration({ ...calibration, image_note: v })} />
                {calibration.calibration_method === '手动比例' && (
                  <Input label="每像素 mm" type="number" placeholder="例如 0.05，表示 1px=0.05mm" value={calibration.mm_per_pixel} onChange={(v) => setCalibration({ ...calibration, mm_per_pixel: v })} />
                )}
                {calibration.calibration_method === '两点比例尺' && (
                  <>
                    <Input label="A点 X/px" type="number" placeholder="点击图片取点后自动填写" value={calibration.point_a_x} onChange={(v) => setCalibration({ ...calibration, point_a_x: v })} />
                    <Input label="A点 Y/px" type="number" placeholder="点击图片取点后自动填写" value={calibration.point_a_y} onChange={(v) => setCalibration({ ...calibration, point_a_y: v })} />
                    <Input label="B点 X/px" type="number" placeholder="点击图片取点后自动填写" value={calibration.point_b_x} onChange={(v) => setCalibration({ ...calibration, point_b_x: v })} />
                    <Input label="B点 Y/px" type="number" placeholder="点击图片取点后自动填写" value={calibration.point_b_y} onChange={(v) => setCalibration({ ...calibration, point_b_y: v })} />
                    <Input label="两点实际距离/mm" type="number" placeholder="请输入 A-B 两点实际距离" value={calibration.scale_distance_mm} onChange={(v) => setCalibration({ ...calibration, scale_distance_mm: v })} />
                    <button className="ghost" onClick={() => setCalibration({ ...calibration, pickingPoint: 'A' })}>从图上取点</button>
                  </>
                )}
                <Input label="标定说明" placeholder="请输入比例尺来源或人工说明" value={calibration.calibration_note} onChange={(v) => setCalibration({ ...calibration, calibration_note: v })} />
                <button className="primary" onClick={saveCalibration} disabled={!selectedImage || !active?.images?.length || busy}><Ruler size={17} />{actionName === '整批标定' ? busyText : '保存整批标定'}</button>
              </div>
              <div className="calibrationHint">
                <strong>当前取点状态</strong>
                <p>{calibration.calibration_method === '两点比例尺' ? (calibration.pickingPoint ? `请在左侧图片上点击 ${calibration.pickingPoint} 点` : '点击“从图上取点”后，依次选择 A 点和 B 点，再填写两点实际距离并保存整批标定。') : '手动比例适合已知每像素代表多少毫米；未标定时报告只显示像素坐标。当前设置保存后统一用于整批图片。'}</p>
              </div>
            </div>
          </div>
        </section>
        }

        {activeSection === 'review' && <section className="pageStack">
        <section className="reviewWorkbench">
          <section className="panel reviewImagePanel">
            <div className="panelHeader">
              <h2><FileImage size={19} />复核图像</h2>
              <div className="realtimeImageActions">
                {selectedImage?.original_url && <>
                  <button className={reviewImageView === 'thresholded' ? 'primary compact' : 'ghost compact'} onClick={() => setReviewImageView('thresholded')}>增强成像（推荐）</button>
                  <button className={reviewImageView === 'original' ? 'primary compact' : 'ghost compact'} onClick={() => setReviewImageView('original')}>原始成像</button>
                </>}
                <select value={selectedImage?.id || ''} onChange={(e) => setSelectedImageId(e.target.value)}>
                  {(active?.images || []).map((image) => <option key={image.id} value={image.id}>{image.original_name}</option>)}
                </select>
              </div>
            </div>
            <div className="imageStage reviewStage">
              {selectedImage ? (
                <div
                  className={`reviewImageWrap ${manualDrawEnabled ? 'drawingEnabled' : ''}`}
                  onPointerDown={startManualBox}
                  onPointerMove={updateManualBox}
                  onPointerUp={finishManualBox}
                  onPointerCancel={finishManualBox}
                >
                  <img ref={reviewImageRef} src={`${API}${reviewImageView === 'original' && selectedImage.original_url ? selectedImage.original_url : (selectedImage.annotated_url || selectedImage.thresholded_url || selectedImage.url)}`} alt={reviewImageView === 'original' ? '原始未截断复核图像' : '增强阈值截断复核图像'} draggable="false" />
                  {visibleDraftBox && <span className="draftBox" style={visibleDraftBox} />}
                </div>
              ) : (
                <div className="empty"><Search size={36} />请选择检测图片</div>
              )}
            </div>
            <div className="selectedDefectCard">
              <strong>{selectedDefect ? selectedDefect.class_name : '未选择缺陷'}</strong>
              <span>{selectedDefect ? `状态：${selectedDefect.review_status}；像素框：(${fmt(selectedDefect.x_min)}, ${fmt(selectedDefect.y_min)}) - (${fmt(selectedDefect.x_max)}, ${fmt(selectedDefect.y_max)})` : selectedImage ? `当前图片已有 ${selectedImageDefects.length} 个缺陷记录。可点击“框选补录”在图上直接拖出小缺陷区域。` : '请选择检测图片。'}</span>
            </div>
          </section>

          <section className="panel reviewSidePanel">
            <div className="panelHeader">
              <h2><CheckCircle2 size={19} />缺陷复核</h2>
              <span>待复核 {pendingCount} · 已确认 {reviewedCount} · 误检 {falsePositiveCount}</span>
            </div>
            <div className="pageIntro">
              <strong>复核建议</strong>
              <p>{defectCount
                ? '先处理“待复核”，点击缺陷行会同步左侧图像。大量结果可搜索和分页，必要时对筛选结果批量确认。'
                : active?.job?.status === '无需复核'
                  ? '本批次未检出需记录异常，当前无需逐项复核；可按既定检验周期管理。'
                  : '当前结果质量不足以形成有效筛查结论，请检查处理记录并补充检测。'}</p>
            </div>
            <div className="reviewActionGroup">
              <strong>筛选缺陷</strong>
              <div className="reviewToolbar">
              <div className="segmented">
                {['待复核', '已确认', '误检', '全部'].map((item) => (
                  <button key={item} className={reviewFilter === item ? 'active' : ''} onClick={() => { setReviewFilter(item); setReviewPage(1); }}>{item}</button>
                ))}
              </div>
              <input className="searchInput" placeholder="搜索图片、区域、类型、备注" value={reviewQuery} onChange={(e) => { setReviewQuery(e.target.value); setReviewPage(1); }} />
              </div>
            </div>
            <div className="reviewActionGroup">
              <strong>当前图片补录</strong>
              <div className="reviewToolbar">
              <button className={manualDrawEnabled ? 'primary' : 'ghost'} onClick={() => { setManualDrawEnabled(!manualDrawEnabled); setMessage(!manualDrawEnabled ? '请在左侧图片上按住鼠标拖出缺陷外接矩形' : '已退出图片框选'); }} disabled={!selectedImage || busy}>{manualDrawEnabled ? '正在框选' : '框选补录'}</button>
              <button className="primary" onClick={createManualDefect} disabled={!selectedImage || busy}><PlusSquare size={17} />{actionName === '保存补录' ? busyText : '保存补录'}</button>
              <button className="ghost" onClick={() => { setDraftBox(null); setManualDrawEnabled(false); setDefectForm({ ...defectForm, x_min: '', y_min: '', x_max: '', y_max: '' }); setMessage('已清空当前补录框'); }} disabled={busy}>清空当前框</button>
              </div>
              <span className="actionHint">{selectedImage ? '先点“框选补录”，再在左侧图片上拖出缺陷区域，最后点“保存补录”。' : '请先选择图片。'}</span>
            </div>
            <div className="reviewActionGroup">
              <strong>已选缺陷操作</strong>
              <div className="reviewToolbar">
              <button className="ghost" onClick={() => markSelectedDefect('已确认')} disabled={!selectedDefect || busy}>{actionName === '确认缺陷' ? busyText : '确认该缺陷'}</button>
              <button className="ghost" onClick={() => markSelectedDefect('误检')} disabled={!selectedDefect || busy}>{actionName === '标记误检' ? busyText : '标为误检'}</button>
              <button className="ghost" onClick={saveDefectEdit} disabled={!selectedDefect || busy}><Save size={17} />{actionName === '保存修改' ? busyText : '保存修改'}</button>
              <button className="danger" onClick={deleteSelectedDefect} disabled={!selectedDefect || busy}><Trash2 size={17} />{actionName === '删除缺陷' ? busyText : '删除缺陷'}</button>
              </div>
              <span className="actionHint">{selectedDefect ? '正在编辑选中的缺陷记录。' : '请先在下方表格选择一条缺陷记录。'}</span>
            </div>
            <div className="reviewActionGroup">
              <strong>批量操作</strong>
              <div className="reviewToolbar">
              <button className="ghost" onClick={() => batchUpdateVisibleDefects('已确认')} disabled={!filteredDefects.length || busy}>{actionName === '批量确认' ? busyText : `批量确认当前筛选结果（${filteredDefects.length}）`}</button>
              <button className="ghost" onClick={() => batchUpdateVisibleDefects('误检')} disabled={!filteredDefects.length || busy}>{actionName === '批量标记误检' ? busyText : `批量标为误检（${filteredDefects.length}）`}</button>
              </div>
            </div>
            {!defectCount && (
              <div className={`zeroDetectionNotice ${active?.job?.status === '无需复核' ? 'noReviewRequired' : ''}`}>
                <strong>{active?.job?.status === '无需复核' ? '未检出需记录异常' : '当前批次未形成有效缺陷记录'}</strong>
                <span>{active?.job?.status === '无需复核'
                  ? '检测流程与结果完整性检查已通过，无需追加专项复检，按既定检验周期管理。'
                  : '请检查数据完整性和处理质量；必要时补充检测后重新生成报告。'}</span>
              </div>
            )}
            <div className="manualDefectEditor">
              <div className="manualDefectTip">
                <strong>人工补录/修正缺陷</strong>
                <span>坐标填写图片像素位置：左上角为起点，X 向右增大，Y 向下增大。只知道大概位置时，先填外接矩形四个边界像素，保存后再按图复核。</span>
              </div>
              <div className="reviewGrid compactReviewGrid">
                <Select label="缺陷类型" value={defectForm.class_name} onChange={(v) => setDefectForm({ ...defectForm, class_name: v })} options={[
                  ['人工标注缺陷', '人工标注缺陷'],
                  ['疑似缺陷', '疑似缺陷'],
                  ['裂纹', '裂纹'],
                  ['气孔', '气孔'],
                  ['夹渣', '夹渣'],
                  ['未熔合', '未熔合'],
                  ['其他', '其他']
                ]} />
                <Input label="左边界 X 像素" type="number" placeholder="矩形左边" value={defectForm.x_min} onChange={(v) => setDefectForm({ ...defectForm, x_min: v })} />
                <Input label="上边界 Y 像素" type="number" placeholder="矩形上边" value={defectForm.y_min} onChange={(v) => setDefectForm({ ...defectForm, y_min: v })} />
                <Input label="右边界 X 像素" type="number" placeholder="矩形右边" value={defectForm.x_max} onChange={(v) => setDefectForm({ ...defectForm, x_max: v })} />
                <Input label="下边界 Y 像素" type="number" placeholder="矩形下边" value={defectForm.y_max} onChange={(v) => setDefectForm({ ...defectForm, y_max: v })} />
                <Input label="处理建议" placeholder="请输入建议，如：建议复检、打磨后复测" value={defectForm.suggestion} onChange={(v) => setDefectForm({ ...defectForm, suggestion: v })} />
                <Input label="备注" placeholder="请输入人工判断依据，如：疑似边缘噪声" value={defectForm.remark} onChange={(v) => setDefectForm({ ...defectForm, remark: v })} />
              </div>
            </div>
            <div className="tableWrap compactTableWrap">
              <table>
              <thead>
                <tr>
                  <th>序号</th>
                  <th>图片</th>
                  <th>缺陷类型</th>
                  <th>置信度</th>
                  <th>像素中心</th>
                  <th>物理中心/mm</th>
                  <th>物理尺寸/mm</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {pagedDefects.map((defect, index) => {
                  const image = defect.image;
                  return (
                    <tr
                      key={defect.id}
                      className={selectedDefectId === defect.id ? 'selectedRow' : ''}
                      onClick={() => {
                        setSelectedImageId(defect.image_id);
                        setSelectedDefectId(defect.id);
                      }}
                    >
                      <td>{(currentReviewPage - 1) * reviewPageSize + index + 1}</td>
                      <td>{image?.original_name || '-'}</td>
                      <td>{defect.class_name}</td>
                      <td>{fmtConfidence(defect.confidence, defect.confidence_available)}</td>
                      <td>({fmt(defect.center_x_px)}, {fmt(defect.center_y_px)})</td>
                      <td>({fmt(defect.center_x_mm)}, {fmt(defect.center_y_mm)})</td>
                      <td>{fmt(defect.width_mm)} x {fmt(defect.height_mm)}</td>
                      <td><span className={`status ${defect.review_status === '已确认' ? 'ok' : defect.review_status === '误检' ? 'bad' : ''}`}>{defect.review_status}</span></td>
                      <td>
                        <button className="mini" onClick={(e) => { e.stopPropagation(); updateDefect(defect.id, { review_status: '已确认' }); }}>确认</button>
                        <button className="mini warn" onClick={(e) => { e.stopPropagation(); updateDefect(defect.id, { review_status: '误检' }); }}>误检</button>
                      </td>
                    </tr>
                  );
                })}
                {!pagedDefects.length && (
                  <tr><td colSpan="9" className="emptyRow">当前筛选条件下暂无缺陷结果。</td></tr>
                )}
              </tbody>
              </table>
            </div>
            <div className="pager">
              <button className="ghost" disabled={currentReviewPage <= 1} onClick={() => setReviewPage(currentReviewPage - 1)}>上一页</button>
              <span>第 {currentReviewPage} / {reviewTotalPages} 页，共 {filteredDefects.length} 条</span>
              <button className="ghost" disabled={currentReviewPage >= reviewTotalPages} onClick={() => setReviewPage(currentReviewPage + 1)}>下一页</button>
            </div>
          </section>
        </section>
        </section>}

        {activeSection === 'settings' && <section className="pageStack settingsPage">
          <section className="panel">
            <div className="panelHeader">
              <h2><Settings2 size={19} />应用设置</h2>
              <button className="ghost compact" onClick={loadCapabilities}><RefreshCw size={16} />重新检查</button>
            </div>
            <div className="helpBox">
              <strong>配置说明</strong>
              <p>设置保存在当前项目或便携程序目录的 config 文件夹中。API Key 写入本地 secrets.env，不提交仓库；保存后立即生效。</p>
            </div>
            <div className="capabilityGrid">
              <StatusItem label="PAUT 成像" value={capabilities?.imaging?.available ? '可用' : '当前不可用'} ok={capabilities?.imaging?.available} />
              <StatusItem label="Agent" value={capabilities?.agent?.available ? '可用' : '未启用或未配置'} ok={capabilities?.agent?.available} />
              <StatusItem label="数据目录" value={capabilities?.paths?.user_data || '读取中'} ok={Boolean(capabilities?.paths?.user_data)} />
            </div>
          </section>

          <section className="panel">
            <div className="panelHeader"><h2>智能分析</h2><span>填写所用服务商的连接信息</span></div>
            <div className="settingsGrid">
              <label className="checkField"><input type="checkbox" checked={appSettings.agent_enabled} onChange={(e) => updateAppSetting('agent_enabled', e.target.checked)} /><span>启用 Agent</span></label>
              <Input label="API 地址" value={appSettings.openai_base_url} onChange={(v) => updateAppSetting('openai_base_url', v)} placeholder="请输入兼容接口的 /v1 地址" />
              <Input label="模型名称" value={appSettings.openai_model} onChange={(v) => updateAppSetting('openai_model', v)} placeholder="请输入服务商提供的模型名称" />
              <Input label={appSettings.openai_api_key_set ? 'API Key（已保存，留空则不修改）' : 'API Key'} value={appSettings.openai_api_key} onChange={(v) => updateAppSetting('openai_api_key', v)} placeholder="请输入 API Key" type="password" />
            </div>
          </section>

          <section className="panel">
            <div className="panelHeader"><h2>成像与保存位置</h2></div>
            <div className="settingsGrid">
              <Input label="MATLAB 可执行文件" value={appSettings.matlab_exe} onChange={(v) => updateAppSetting('matlab_exe', v)} placeholder="matlab.exe 或完整路径" />
              <Input label="输出目录" value={appSettings.output_dir} onChange={(v) => updateAppSetting('output_dir', v)} placeholder="请输入输出目录" />
            </div>
            <details className="settingsAdvanced"><summary>高级成像设置</summary><Input label="Runtime 组件模块" value={appSettings.matlab_runtime_module} onChange={(v) => updateAppSetting('matlab_runtime_module', v)} placeholder="仅在提供编译组件时填写，通常无需修改" /></details>
            <div className="settingsActions">
              <button className="primary" disabled={busy} onClick={saveAppSettings}><Save size={17} />{actionName === '保存设置' ? '正在保存...' : '保存设置'}</button>
              <button className="ghost" disabled={busy} onClick={() => testAppCapability('agent')}><Lightbulb size={17} />{actionName === '测试Agent' ? '正在测试...' : '测试智能分析连接'}</button>
              <button className="ghost" disabled={busy} onClick={() => testAppCapability('imaging')}><Activity size={17} />{actionName === '检查成像' ? '正在检查...' : '检查成像环境'}</button>
              {desktopBridge && <button className="ghost" onClick={() => desktopBridge.openOutputDirectory()}><FolderOpen size={17} />打开输出目录</button>}
            </div>
          </section>
        </section>}

        {activeSection === 'report' && <section className="pageStack">
        <section className="panel agentPanel">
          <div className="panelHeader">
            <h2><Lightbulb size={19} />Agent 分析</h2>
            <div className="agentHeaderActions">
              <button className="ghost agentRunButton" onClick={runAgentAnalysis} disabled={!active || busy || ['提交中', '分析中'].includes(agentStatus)}>
                <Lightbulb size={17} />
                {['提交中', '分析中'].includes(agentStatus) ? '分析进行中...' : '开始Agent分析'}
              </button>
              <button className="primary agentReportButton" onClick={generateAgentReport} disabled={!active || busy || agentStatus === '分析中'}>
                <Download size={17} />
                {actionName === '生成报告' ? busyText : '生成检测报告'}
              </button>
            </div>
          </div>
          <div className={`agentStatus ${['提交中', '分析中'].includes(agentStatus) ? 'running' : ['分析失败', '大模型调用失败', '状态读取失败'].includes(agentStatus) ? 'bad' : agentStatus === '分析完成' ? 'ok' : agentStatus === '本地摘要可用' ? 'local' : ''}`} aria-live="polite">
            <strong>{agentStatus}</strong>
            <span>{agentStatus === '提交中' ? '正在启动分析，请稍候。' : agentStatus === '分析中' ? '正在分析检测结果，完成后自动显示。' : agentStatus === '分析完成' ? '分析完成，可查看结论并生成报告。' : ['本地摘要可用', '大模型调用失败'].includes(agentStatus) ? '智能服务暂不可用，当前显示本地分析结果。可在设置中检查连接后重试。' : agentStatus === '分析失败' ? '分析未完成，请检查服务连接后重试。' : '点击“开始Agent分析”查看本批次结论。'}</span>
            {['提交中', '分析中'].includes(agentStatus) && <div className="agentProgress" aria-hidden="true"><i /></div>}
          </div>
          {agentResult && <AgentAnalysisDetails result={agentResult} />}
        </section>

        <section className="panel auditPanel">
          <div className="panelHeader">
            <h2><FileText size={19} />报告辅助操作</h2>
            <span>导出与归档</span>
          </div>
          <div className="reportActions">
            <button className="ghost" onClick={exportDefectsCsv} disabled={!active || busy}><Download size={17} />导出明细</button>
            <button className="ghost" onClick={generateArchive} disabled={!active || busy}><Download size={17} />生成归档</button>
          </div>
        </section>

        </section>}
      </section>
    </main>
  );
}

function WaveformChart({ data }) {
  const width = 900;
  const height = 260;
  const padding = { left: 54, right: 18, top: 18, bottom: 34 };
  const times = data.time_us || [];
  const amplitudes = data.amplitude || [];
  const envelope = data.envelope || [];
  if (!times.length) return <div className="emptyRow">当前通道没有可显示的采样点。</div>;
  const maxTime = times[times.length - 1] || 1;
  const maxAmplitude = Math.max(...envelope.map(Math.abs), ...amplitudes.map(Math.abs), Number.EPSILON);
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const x = (time) => padding.left + (time / maxTime) * plotWidth;
  const y = (value) => padding.top + plotHeight / 2 - (value / maxAmplitude) * plotHeight * 0.46;
  const points = amplitudes.map((value, index) => `${x(times[index]).toFixed(2)},${y(value).toFixed(2)}`).join(' ');
  const envelopePoints = envelope.map((value, index) => `${x(times[index]).toFixed(2)},${y(value).toFixed(2)}`).join(' ');
  return (
    <div className="waveformChart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${data.tx_label}到${data.rx_label}的A扫波形`}>
        <rect x={padding.left} y={padding.top} width={plotWidth} height={plotHeight} className="plotBackground" />
        {(data.gates || []).map((gate) => (
          <g key={gate.name}>
            <rect x={x(gate.start_us)} y={padding.top} width={Math.max(1, x(gate.end_us) - x(gate.start_us))} height={plotHeight} className="gateArea" />
            <text x={Math.min(x(gate.start_us) + 4, width - 130)} y={padding.top + 14} className="gateLabel">{gate.name}</text>
          </g>
        ))}
        <line x1={padding.left} y1={y(0)} x2={width - padding.right} y2={y(0)} className="zeroLine" />
        <polyline points={points} className="signalLine" />
        <polyline points={envelopePoints} className="envelopeLine" />
        <text x={padding.left} y={height - 10} className="axisLabel">0 μs</text>
        <text x={width - padding.right} y={height - 10} textAnchor="end" className="axisLabel">{fmt(maxTime)} μs</text>
      </svg>
      <div className="waveformLegend"><span className="signalKey">原始波形</span><span className="envelopeKey">包络</span><span>原始 {data.sample_count} 点 / 显示 {data.display_count} 点</span></div>
    </div>
  );
}

function Metric({ label, value }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function AgentAnalysisDetails({ result }) {
  const frameAnalyses = Array.isArray(result.frame_analyses) ? result.frame_analyses : [];
  const candidates = Array.isArray(result.response_regions) ? result.response_regions : (Array.isArray(result.defect_instances) ? result.defect_instances : (Array.isArray(result.fusion_candidates) ? result.fusion_candidates : (Array.isArray(result.global_defect_candidates) ? result.global_defect_candidates : [])));
  const displayedFrames = frameAnalyses.length ? frameAnalyses : Object.values(candidates.reduce((groups, candidate) => {
    const frameId = candidate.frame_id || candidate.frame_ids?.[0] || '当前帧';
    groups[frameId] ||= { frame_id: frameId, summary: '', response_regions: [] };
    groups[frameId].response_regions.push(candidate);
    return groups;
  }, {}));
  const assessments = Array.isArray(result.defect_assessments) ? result.defect_assessments : [];
  const assessmentById = Object.fromEntries(assessments.map((item) => [item?.candidate_id, item]));
  const risks = asAgentList(result.risks);
  const riskAssessment = result.risk_assessment && typeof result.risk_assessment === 'object' ? result.risk_assessment : {};
  const riskLevel = result.risk_level || riskAssessment.risk_level || '未评估';
  const actionLabels = {
    routine_management: '按既定检验计划管理',
    focused_recheck: '针对异常区域复检',
    engineering_assessment: '维修或工程完整性评估',
    suspend_and_assess: '暂停使用并开展专业评估'
  };
  const recommendedAction = result.recommended_action_text || riskAssessment.recommendation || actionLabels[result.recommended_action] || '等待评价';
  const confidenceLevel = result.confidence_level || riskAssessment.confidence_level || riskAssessment.confidence || '未评估';
  const detectionState = result.detection_state || riskAssessment.detection_state;
  const riskReasons = asAgentList(riskAssessment.reasons);
  const potentialRisks = asAgentList(result.potential_risks || riskAssessment.potential_risks);
  const candidateRiskById = Object.fromEntries((riskAssessment.candidate_risks || []).map((item) => [item?.candidate_id, item]));
  const suggestions = asAgentList(result.review_suggestions || result.suggestions);
  const boundaries = [...asAgentList(result.missing_conditions), ...asAgentList(result.uncertainty), ...asAgentList(result.guardrail_warnings)];
  const imageOnly = Array.isArray(result.auxiliary_image_findings) ? result.auxiliary_image_findings : [];
  const sources = asAgentList(result.sources);
  const receiptTimes = displayedFrames.map(frame => frame.scan_receipt?.received_at || frame.received_at).filter(value => value && Number.isFinite(Date.parse(value))).sort((a, b) => Date.parse(a) - Date.parse(b));
  const scanTime = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未记录';

  return (
    <div className="agentDetails">
      {result.scan_summary?.received_frame_count > 0 && <p>模拟采集，共 {result.scan_summary.received_frame_count} 帧。扫查距离为估算值。{!result.scan_summary.passed && '采集记录异常，请检查后再生成报告。'}</p>}
      <section className="agentSummary">
        <strong>总体结论</strong>
        <p>起始扫查时间：{scanTime(receiptTimes[0])} · 结束扫查时间：{scanTime(receiptTimes.at(-1))}</p>
        <p>{displaySummary(result.conclusion || riskAssessment.conclusion || result.summary, '未生成摘要')}</p>
        {(result.frame_count != null || result.batch_id) && (
          <span className="agentMeta">本次共分析 {result.frame_count ?? '-'} 帧</span>
        )}
      </section>

      <section className={`agentRiskOverview riskLevel-${riskLevel}`}>
        <div className="agentRiskHeading">
          <div><span>辅助风险等级</span><strong>{riskLevel}</strong></div>
          <div><span>处置建议</span><strong>{recommendedAction}</strong></div>
          <div><span>可信程度</span><strong>{confidenceLevel}</strong></div>
        </div>
        {confidenceLevel === '低' && <p>当前证据不足以支持精确定量或最终定性。{['高', '严重'].includes(riskLevel) ? '异常线索仍需优先处置，应先核实类型与尺寸，再由专业人员决定维修或停用措施。' : '尺寸和类型仅供参考，应补充检测证据后再作最终判断。'}人工确认表示记录已审核，不代表测量误差已消除。</p>}
        {!!riskReasons.length && <div className="agentRiskReasons"><b>主要依据</b><ul>{riskReasons.map((item, index) => <li key={`risk-reason-${index}`}>{displayAgentItem(item)}</li>)}</ul></div>}
        <p className="riskBoundary">{riskAssessment.decision_boundary || '该结果仅用于辅助复核，不构成自动验收或判废结论。'}</p>
      </section>

      <section className="agentDetailSection">
        <h3>逐帧检测结果</h3>
        {!candidates.length && detectionState === 'no_recordable_indication' && <div className="agentEmptyNotice noIndication">本次辅助检测未检出需记录异常，无需追加专项复检，按既定检验周期管理。</div>}
        {!candidates.length && detectionState !== 'no_recordable_indication' && <div className="agentEmptyNotice">当前数据不足以形成有效筛查结论，建议补充检测并检查结果完整性。</div>}
        {displayedFrames.map((frame, frameIndex) => (
          <div className="agentFrameBlock" key={frame.frame_id || frameIndex}>
            <div className="agentFrameHeading">
              <strong>第 {frame.frame_index != null ? frame.frame_index + 1 : frameIndex + 1} 帧 · {operatorText(frame.frame_type || '类型未确定')}</strong>
              <span>{displaySummary(frame.summary, `发现 ${(frame.response_regions || frame.defect_instances || []).length} 处疑似缺陷`)}</span>
            </div>
            <p className="agentMeta">扫查时间：{scanTime(frame.scan_receipt?.received_at || frame.received_at)}{frame.scan_receipt?.estimated_scan_distance_mm != null && ` · 估算扫查距离 ${fmt(frame.scan_receipt.estimated_scan_distance_mm)} mm（非缺陷定位）`}</p>
            <div className="agentGroupGrid">
              {(frame.response_regions || frame.defect_instances || []).map((candidate, index) => {
                const candidateId = candidate.defect_id || candidate.candidate_id;
                const assessment = assessmentById[candidateId] || {};
                const candidateRisk = candidateRiskById[candidateId] || {};
                const geometry = candidate.geometry || {};
                const diameter = candidate.diameter_mm ?? geometry.diameter_mm;
                const sectionLength = candidate.length_mm ?? geometry.length_mm ?? candidate.section_length_estimate_mm;
                const sectionWidth = candidate.width_mm ?? geometry.width_mm ?? candidate.section_width_estimate_mm;
                const sectionOrientation = candidate.orientation_deg ?? geometry.orientation_deg ?? candidate.section_orientation_deg;
                const ascanSpan = candidate.ascan_response_span_mm;
                const possibleCauses = asAgentList(assessment.possible_causes);
                const reviewFocus = asAgentList(assessment.review_focus);
                const sizeText = diameter != null
                  ? `面积等效直径约 ${fmt(diameter)} mm${geometry.major_axis_mm && geometry.minor_axis_mm ? `，主轴/次轴 ${fmt(geometry.major_axis_mm)} / ${fmt(geometry.minor_axis_mm)} mm` : ''}`
                  : sectionLength != null || sectionWidth != null
                    ? [sectionLength != null ? `长约 ${fmt(sectionLength)} mm` : '', sectionWidth != null ? `宽约 ${fmt(sectionWidth)} mm` : ''].filter(Boolean).join('，')
                    : ascanSpan != null ? `A扫响应范围约 ${fmt(ascanSpan)} mm` : '边界不足，暂不估计尺寸';
                return (
                  <article className="agentGroupCard" key={candidateId || index}>
                    <div className="candidateTitle">
                    <strong>疑似缺陷 {index + 1}</strong>
                      <span>{candidate.review_status || candidate.review?.status || (result.review_requirement === 'completed' ? '已复核' : '待复核')}</span>
                    </div>
                    {candidateRisk.risk_level && <div className="candidateRiskLine"><span>风险 {candidateRisk.risk_level}</span><b>{candidateRisk.recommendation}</b></div>}
                    <dl className="candidateMetrics">
                      <dt>缺陷类型</dt><dd>{operatorText(candidate.type || assessment.probable_type || candidateRisk.defect_hint || '类型未确定')}</dd>
                      <dt>位置</dt><dd>x = {fmt(candidate.x_mm ?? candidate.center_mm?.x ?? candidate.centroid_mm?.x)} mm，深度 z = {fmt(candidate.z_mm ?? candidate.center_mm?.z ?? candidate.centroid_mm?.z)} mm</dd>
                      {candidate.peak_x_mm != null && candidate.peak_z_mm != null && <><dt>峰值位置</dt><dd>x = {fmt(candidate.peak_x_mm)} mm，深度 z = {fmt(candidate.peak_z_mm)} mm</dd></>}
                      <dt>二维尺寸</dt><dd>{sizeText}</dd>
                      {sectionOrientation != null && candidate.family !== 'pore' && candidate.type !== '气孔' && <><dt>截面朝向</dt><dd>约 {fmt(sectionOrientation)}°</dd></>}
                      <dt>简要依据</dt><dd>{operatorText(candidate.brief_evidence || candidate.type_basis_short || '根据校验后的检测结果综合判断')}</dd>
                      {!!possibleCauses.length && <><dt>可能成因</dt><dd>{displayAgentItem(possibleCauses[0])}</dd></>}
                    </dl>
                    {!!reviewFocus.length && !['已确认', '已复核', '误检'].includes(candidate.review_status || candidate.review?.status) && result.review_requirement !== 'completed' && <p className="candidateNote">待确认：{displayAgentItem(reviewFocus[0])}</p>}
                  </article>
                );
              })}
            </div>
          </div>
        ))}
        {!!imageOnly.length && <div className="imageOnlyNotice">另有 {imageOnly.length} 处图像异常需要确认。</div>}
        {!!candidates.length && <p className="measurementBoundary">尺寸与朝向为当前二维截面估计。</p>}
      </section>

      <div className="agentDetailColumns">
        <AgentReadableList title="潜在风险" items={potentialRisks.length ? potentialRisks : risks} empty="当前证据不足以确定具体失效风险，仍需结合适用标准和人工复核。" />
      <AgentReadableList title="后续建议" items={suggestions} empty={recommendedAction} />
      </div>


      {!!(boundaries.length || sources.length) && <details className="agentTechnicalDetails">
        <summary>查看适用条件、分析边界和参考资料</summary>
        <AgentReadableList title="适用条件与边界" items={boundaries} empty="暂无补充说明。" />
        {!!sources.length && <AgentReadableList title="参考资料" items={sources} />}
      </details>}
    </div>
  );
}

function AgentReadableList({ title, items, empty = '' }) {
  const values = asAgentList(items);
  return (
    <section className="agentDetailSection">
      <h3>{title}</h3>
      {values.length ? <ul>{values.map((item, index) => <li key={`${title}-${index}`}>{displayAgentItem(item)}</li>)}</ul> : <p className="agentListEmpty">{empty}</p>}
    </section>
  );
}

function asAgentList(value) {
  if (value === null || value === undefined || value === '') return [];
  return Array.isArray(value) ? value : [value];
}

function displaySummary(value, empty = '-') {
  if (value === null || value === undefined || value === '') return empty;
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return operatorText(String(value));
  if (Array.isArray(value)) return value.map((item) => displaySummary(item, '')).filter(Boolean).join('；') || empty;
  if (typeof value === 'object') {
    const total = value.count ?? value.total ?? value.defect_count;
    const counts = value.counts && typeof value.counts === 'object'
      ? Object.entries(value.counts).filter(([, count]) => Number(count) > 0).map(([name, count]) => `${name} ${count}处`)
      : [];
    const prefix = total != null ? `共 ${total} 处检测结果` : '';
    const warning = Number(value.warning_count || 0) > 0 ? `${value.warning_count} 条质量提示` : '';
    const statistical = [prefix, counts.join('、'), warning].filter(Boolean).join('；');
    if (statistical) return statistical;
    const preferred = value.title || value.name || value.label;
    if (preferred != null) return displaySummary(preferred, empty);
    if (value.summary !== undefined && value.summary !== value) return displaySummary(value.summary, empty);
    return '结构化分析结果已生成';
  }
  return String(value);
}

function displayAgentItem(value) {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return operatorText(String(value));
  if (Array.isArray(value)) return value.map(displayAgentItem).join('；');
  if (typeof value === 'object') {
    const preferred = value.title || value.name || value.code || value.label || value.summary;
    const url = value.url || value.source_url;
    if (preferred && url) return `${preferred}（${url}）`;
    if (preferred) return String(preferred);
    return '详情见检测报告。';
  }
  return String(value);
}

function operatorText(value) {
  return String(value)
    .replaceAll('团队算法判定为', '检测结果提示为')
    .replaceAll('团队算法输出', '检测结果')
    .replaceAll('团队算法结果', '检测结果')
    .replaceAll('团队标注图', '缺陷标注图')
    .replaceAll('团队未提供', '未提供')
    .replaceAll('团队结果', '检测结果')
    .replaceAll('（团队算法）', '')
    .replaceAll('响应区域', '疑似缺陷');
}

function StatusItem({ label, value, ok, title }) {
  return (
    <div className={`statusItem ${ok ? 'ok' : 'bad'}`} title={title || ''}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Input({ label, value, onChange, placeholder = '', type = 'text' }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type={type} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function Select({ label, value, onChange, options }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([optionValue, text]) => <option key={optionValue} value={optionValue}>{text}</option>)}
      </select>
    </label>
  );
}

function fmt(value) {
  if (value === null || value === undefined || value === '') return '-';
  return Number(value).toFixed(2);
}

function fmtConfidence(value, available = true) {
  if (!available || value === null || value === undefined || value === '') return '未提供';
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 && number <= 1
    ? `${(number * 100).toFixed(1)}%`
    : String(value);
}

async function readApiError(response) {
  const text = await response.text();
  try {
    const payload = JSON.parse(text);
    return payload.detail || text;
  } catch {
    return text;
  }
}

createRoot(document.getElementById('root')).render(<AppErrorBoundary><App /></AppErrorBoundary>);
