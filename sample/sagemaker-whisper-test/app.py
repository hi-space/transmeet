#!/usr/bin/env python3
"""Simple web UI for testing SageMaker Whisper endpoint with live mic input."""

import json
import subprocess
import tempfile
import boto3
from http.server import HTTPServer, BaseHTTPRequestHandler

REGION = "us-east-1"
ENDPOINT_NAME = "whisper-large"

HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>Whisper SageMaker Tester</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }
  h1 { font-size: 1.5rem; margin-bottom: 20px; color: #f8fafc; }
  .container { max-width: 900px; margin: 0 auto; }
  .card { background: #1e293b; border-radius: 12px; padding: 24px; margin-bottom: 20px; border: 1px solid #334155; }
  .card h2 { font-size: 1.1rem; margin-bottom: 16px; color: #94a3b8; }
  label { display: block; font-size: 0.85rem; color: #94a3b8; margin-bottom: 4px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .field { display: flex; flex-direction: column; }
  .field input, .field select { background: #0f172a; border: 1px solid #475569; border-radius: 6px; padding: 8px 10px; color: #f8fafc; font-size: 0.9rem; }
  .field input:focus, .field select:focus { outline: none; border-color: #60a5fa; }
  .field .desc { font-size: 0.75rem; color: #64748b; margin-top: 2px; line-height: 1.4; }
  .mic-area { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  .mic-btn { width: 80px; height: 80px; border-radius: 50%; border: none; cursor: pointer; font-size: 2rem; display: flex; align-items: center; justify-content: center; transition: all 0.2s; }
  .mic-btn.idle { background: #3b82f6; }
  .mic-btn.idle:hover { background: #2563eb; transform: scale(1.05); }
  .mic-btn.recording { background: #ef4444; animation: pulse 1s infinite; }
  @keyframes pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(239,68,68,0.4); } 50% { box-shadow: 0 0 0 16px rgba(239,68,68,0); } }
  .mic-info { font-size: 0.9rem; color: #94a3b8; }
  .mic-info .time { font-size: 1.4rem; font-weight: 600; color: #f8fafc; font-variant-numeric: tabular-nums; }
  details.card > summary { cursor: pointer; list-style: none; }
  details.card > summary::-webkit-details-marker { display: none; }
  details.card > summary h2::before { content: '\25B6'; font-size: 0.7rem; margin-right: 8px; display: inline-block; transition: transform 0.2s; }
  details.card[open] > summary h2::before { transform: rotate(90deg); }
  .result-box { background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 16px; white-space: pre-wrap; font-family: 'SF Mono', Menlo, monospace; font-size: 0.85rem; min-height: 60px; max-height: 400px; overflow-y: auto; line-height: 1.6; }
  .status { font-size: 0.85rem; color: #94a3b8; margin-bottom: 8px; }
  .status.error { color: #f87171; }
  .status.success { color: #4ade80; }
  .visualizer { height: 40px; width: 100%; margin-top: 8px; }
</style>
</head>
<body>
<div class="container">
  <h1>Whisper SageMaker Endpoint Tester</h1>

  <div class="card">
    <h2>Microphone</h2>
    <div class="mic-area">
      <button class="mic-btn idle" id="micBtn" onclick="toggleRecording()">&#127908;</button>
      <div class="mic-info">
        <div id="micStatus">Click to start recording</div>
        <div class="time" id="timer">00:00</div>
      </div>
    </div>
    <canvas id="visualizer" class="visualizer"></canvas>
  </div>

  <details class="card" open>
    <summary><h2 style="display:inline;">Parameters</h2></summary>
    <div class="grid" style="margin-top:12px;">
      <div class="field">
        <label>language</label>
        <select id="p_language">
          <option value="">-- auto detect --</option>
          <option value="korean">korean</option>
          <option value="english" selected>english</option>
          <option value="japanese">japanese</option>
          <option value="chinese">chinese</option>
          <option value="spanish">spanish</option>
          <option value="french">french</option>
          <option value="german">german</option>
          <option value="russian">russian</option>
          <option value="portuguese">portuguese</option>
          <option value="italian">italian</option>
          <option value="indonesian">indonesian</option>
          <option value="hindi">hindi</option>
          <option value="turkish">turkish</option>
          <option value="vietnamese">vietnamese</option>
          <option value="thai">thai</option>
          <option value="arabic">arabic</option>
        </select>
        <div class="desc">음성 언어 지정. 비워두면 자동 감지. ISO 코드가 아닌 풀네임 사용.</div>
      </div>
      <div class="field">
        <label>task</label>
        <select id="p_task">
          <option value="transcribe" selected>transcribe</option>
          <option value="translate">translate</option>
        </select>
        <div class="desc">transcribe: 원본 언어로 텍스트 변환. translate: 영어로 번역.</div>
      </div>
      <div class="field">
        <label>max_new_tokens</label>
        <input type="number" id="p_max_new_tokens" value="444" min="1" max="444">
        <div class="desc">생성할 최대 토큰 수. Whisper 한도 448에서 프롬프트 토큰(~4) 제외하면 최대 444.</div>
      </div>
      <div class="field">
        <label>num_beams</label>
        <input type="number" id="p_num_beams" value="1" min="1" max="10">
        <div class="desc">빔 서치 너비. 1 = 그리디 디코딩(가장 빠름). 높을수록 품질 향상, 속도 저하.</div>
      </div>
      <div class="field">
        <label>temperature</label>
        <input type="number" id="p_temperature" value="0" step="0.1" min="0" max="2">
        <div class="desc">샘플링 무작위성. 0 = 결정적(그리디). 높을수록 다양한 출력.</div>
      </div>
      <div class="field">
        <label>do_sample</label>
        <select id="p_do_sample">
          <option value="false" selected>false</option>
          <option value="true">true</option>
        </select>
        <div class="desc">샘플링 활성화. false = 항상 최고 확률 토큰 선택. true = 확률 분포에서 샘플링.</div>
      </div>
      <div class="field">
        <label>top_p</label>
        <input type="number" id="p_top_p" placeholder="1.0" step="0.05" min="0" max="1">
        <div class="desc">누클리어스 샘플링. 누적 확률이 이 값까지인 토큰만 고려. 1.0 = 필터링 없음.</div>
      </div>
      <div class="field">
        <label>top_k</label>
        <input type="number" id="p_top_k" placeholder="50" min="0">
        <div class="desc">Top-K 샘플링. 확률 상위 K개 토큰만 고려. 0 = 비활성화.</div>
      </div>
      <div class="field">
        <label>no_repeat_ngram_size</label>
        <input type="number" id="p_no_repeat_ngram_size" placeholder="0" min="0">
        <div class="desc">지정 크기의 n-gram 반복 방지. 0 = 비활성화. 3 = 3단어 구문 반복 금지.</div>
      </div>
      <div class="field">
        <label>length_penalty</label>
        <input type="number" id="p_length_penalty" placeholder="1.0" step="0.1">
        <div class="desc">길이에 대한 지수 페널티. &gt;1 = 긴 출력 선호, &lt;1 = 짧은 출력 선호. 빔 서치에서만 적용.</div>
      </div>
      <div class="field">
        <label>early_stopping</label>
        <select id="p_early_stopping">
          <option value="">-- default --</option>
          <option value="false">false</option>
          <option value="true">true</option>
        </select>
        <div class="desc">num_beams개 이상의 문장이 완성되면 빔 서치 조기 종료. 빔 서치에서만 적용.</div>
      </div>
      <div class="field">
        <label>num_return_sequences</label>
        <input type="number" id="p_num_return_sequences" placeholder="1" min="1" max="10">
        <div class="desc">반환할 시퀀스 수. num_beams 이하여야 함.</div>
      </div>
      <div class="field">
        <label>min_length</label>
        <input type="number" id="p_min_length" placeholder="0" min="0">
        <div class="desc">최소 전체 시퀀스 길이(프롬프트 포함). 이 길이 전에는 EOS 토큰 차단.</div>
      </div>
      <div class="field">
        <label>min_new_tokens</label>
        <input type="number" id="p_min_new_tokens" placeholder="0" min="0">
        <div class="desc">최소 생성 토큰 수(프롬프트 제외).</div>
      </div>
      <div class="field">
        <label>max_length</label>
        <input type="number" id="p_max_length" placeholder="448" min="1">
        <div class="desc">최대 전체 길이(프롬프트+생성). max_new_tokens를 덮어씀. 보통 비워둠.</div>
      </div>
      <div class="field">
        <label>max_time</label>
        <input type="number" id="p_max_time" placeholder="" step="0.1" min="0">
        <div class="desc">생성 최대 소요 시간(초). 이 시간이 지나면 생성 중단.</div>
      </div>
    </div>
    <div style="margin-top:14px;">
      <label>forced_decoder_ids (JSON array)</label>
      <input type="text" id="p_forced_decoder_ids" placeholder='e.g. [[1, 50259], [2, 50359]]' style="width:100%; background:#0f172a; border:1px solid #475569; border-radius:6px; padding:8px 10px; color:#f8fafc; font-size:0.9rem;">
      <div style="font-size:0.75rem; color:#64748b; margin-top:2px;">특정 생성 단계에서 강제할 토큰 ID. 형식: [[step, token_id], ...]. 보통 비워둠.</div>
    </div>
  </details>

  <details class="card">
    <summary><h2 style="display:inline;">Request Payload</h2></summary>
    <div class="result-box" id="payloadView" style="margin-top:12px;">-- record audio and it will be sent automatically --</div>
  </details>

  <div class="card">
    <h2>Response</h2>
    <div id="statusLine" class="status"></div>
    <div class="result-box" id="resultView">--</div>
  </div>
</div>

<script>
let mediaRecorder = null;
let audioChunks = [];
let stream = null;
let timerInterval = null;
let startTime = 0;
let analyser = null;
let animFrame = null;

function toggleRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') stopRecording();
  else startRecording();
}

async function startRecording() {
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch (e) { alert('Microphone access denied.'); return; }

  const actx = new AudioContext();
  const source = actx.createMediaStreamSource(stream);
  analyser = actx.createAnalyser();
  analyser.fftSize = 256;
  source.connect(analyser);
  drawVisualizer();

  audioChunks = [];
  mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });
  mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
  mediaRecorder.onstop = () => {
    cancelAnimationFrame(animFrame);
    const blob = new Blob(audioChunks, { type: 'audio/webm' });
    sendAudio(blob);
    stream.getTracks().forEach(t => t.stop());
  };

  mediaRecorder.start();
  startTime = Date.now();
  timerInterval = setInterval(updateTimer, 100);

  document.getElementById('micBtn').className = 'mic-btn recording';
  document.getElementById('micBtn').innerHTML = '&#9632;';
  document.getElementById('micStatus').textContent = 'Recording... click to stop';
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    clearInterval(timerInterval);
    document.getElementById('micBtn').className = 'mic-btn idle';
    document.getElementById('micBtn').innerHTML = '&#127908;';
    document.getElementById('micStatus').textContent = 'Sending to endpoint...';
  }
}

function updateTimer() {
  const elapsed = Math.floor((Date.now() - startTime) / 1000);
  document.getElementById('timer').textContent =
    String(Math.floor(elapsed / 60)).padStart(2, '0') + ':' + String(elapsed % 60).padStart(2, '0');
}

function drawVisualizer() {
  const canvas = document.getElementById('visualizer');
  const ctx = canvas.getContext('2d');
  canvas.width = canvas.offsetWidth;
  canvas.height = canvas.offsetHeight;
  const bufLen = analyser.frequencyBinCount;
  const data = new Uint8Array(bufLen);
  function draw() {
    animFrame = requestAnimationFrame(draw);
    analyser.getByteFrequencyData(data);
    ctx.fillStyle = '#1e293b';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    const barW = canvas.width / bufLen * 2.5;
    let x = 0;
    for (let i = 0; i < bufLen; i++) {
      const h = (data[i] / 255) * canvas.height;
      ctx.fillStyle = `hsl(${210 + data[i] * 0.5}, 80%, 60%)`;
      ctx.fillRect(x, canvas.height - h, barW - 1, h);
      x += barW;
      if (x > canvas.width) break;
    }
  }
  draw();
}

function buildPayload(audioHex) {
  const payload = { audio_input: audioHex };
  const numFields = ['max_new_tokens','num_beams','temperature','top_p','top_k',
    'no_repeat_ngram_size','length_penalty','num_return_sequences','min_length',
    'min_new_tokens','max_length','max_time'];
  const strFields = ['language','task'];
  const boolFields = ['do_sample','early_stopping'];

  for (const f of numFields) {
    const el = document.getElementById('p_' + f);
    if (el && el.value !== '') payload[f] = parseFloat(el.value);
  }
  for (const f of strFields) {
    const el = document.getElementById('p_' + f);
    if (el && el.value !== '') payload[f] = el.value;
  }
  for (const f of boolFields) {
    const el = document.getElementById('p_' + f);
    if (el && el.value !== '') payload[f] = el.value === 'true';
  }
  const fdi = document.getElementById('p_forced_decoder_ids').value.trim();
  if (fdi) {
    try { payload.forced_decoder_ids = JSON.parse(fdi); }
    catch(e) { alert('Invalid JSON for forced_decoder_ids'); return null; }
  }
  return payload;
}

async function sendAudio(blob) {
  const buf = await blob.arrayBuffer();
  const hex = Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');

  const payload = buildPayload(hex);
  if (!payload) { document.getElementById('micStatus').textContent = 'Click to start recording'; return; }

  const displayPayload = { ...payload, audio_input: hex.substring(0, 60) + '... (' + (blob.size / 1024).toFixed(1) + ' KB)' };
  document.getElementById('payloadView').textContent = JSON.stringify(displayPayload, null, 2);
  document.getElementById('statusLine').textContent = '';
  document.getElementById('resultView').textContent = 'Loading...';

  try {
    const start = performance.now();
    const resp = await fetch('/invoke', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const elapsed = ((performance.now() - start) / 1000).toFixed(2);
    const data = await resp.json();
    const statusEl = document.getElementById('statusLine');
    statusEl.className = resp.ok ? 'status success' : 'status error';
    statusEl.textContent = 'HTTP ' + resp.status + ' | ' + elapsed + 's';
    document.getElementById('resultView').textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    document.getElementById('statusLine').className = 'status error';
    document.getElementById('statusLine').textContent = 'Error';
    document.getElementById('resultView').textContent = err.toString();
  }
  document.getElementById('micStatus').textContent = 'Click to start recording';
}
</script>
</body>
</html>
"""

runtime_client = boto3.client("sagemaker-runtime", region_name=REGION)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML.encode())

    def do_POST(self):
        if self.path != "/invoke":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        try:
            payload = json.loads(body)
            audio_hex = payload.pop("audio_input")
            audio_bytes = bytes.fromhex(audio_hex)

            # Convert webm to 16kHz mono WAV via ffmpeg
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f_in:
                f_in.write(audio_bytes)
                in_path = f_in.name
            out_path = in_path.replace(".webm", ".wav")
            subprocess.run(
                ["ffmpeg", "-y", "-i", in_path, "-ar", "16000", "-ac", "1", "-f", "wav", out_path],
                capture_output=True, check=True,
            )
            with open(out_path, "rb") as f_out:
                wav_hex = f_out.read().hex()

            import os
            os.unlink(in_path)
            os.unlink(out_path)

            payload["audio_input"] = wav_hex
            sm_body = json.dumps(payload)

            resp = runtime_client.invoke_endpoint(
                EndpointName=ENDPOINT_NAME,
                ContentType="application/json",
                Body=sm_body,
            )
            result = resp["Body"].read().decode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(result.encode())
        except subprocess.CalledProcessError as e:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"ffmpeg conversion failed: {e.stderr.decode()}"}).encode())
        except Exception as e:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {args[0]}")


if __name__ == "__main__":
    port = 8080
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Whisper Endpoint Tester running at http://localhost:{port}")
    server.serve_forever()
