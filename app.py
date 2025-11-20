import subprocess
import os
import json
import logging
from flask import Flask, request, jsonify

# =====================================================
# 환경 설정
# =====================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

ECR_IMAGE = "934029856517.dkr.ecr.ap-northeast-2.amazonaws.com/web-ai:latest"
DOCKER_BIN = "/usr/bin/docker"
LOG_DIR = os.path.join(os.getcwd(), "worker_logs")
RESULT_DIR = os.path.join(os.getcwd(), "callback_results")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(RESULT_DIR, exist_ok=True)

# =====================================================
# 분석 요청 API
# =====================================================
@app.route("/analyze", methods=["POST"])
def analyze_request():
    try:
        data = request.json
        url_to_analyze = data.get("url")
        callback_url = data.get("callback_url")
        website_id = data.get("website_id")  # optional
        
        if not url_to_analyze or not callback_url:
            return jsonify({"error": "Missing 'url' or 'callback_url'"}), 400

        task_id = os.urandom(8).hex()
        log_path = os.path.join(LOG_DIR, f"{task_id}.log")

        logging.info(f"[{task_id}] Received analyze request for {url_to_analyze}")

        command = [
            DOCKER_BIN, "run", "--rm",
            "-v", f"{RESULT_DIR}:/app/callback_results",
            "--pull=never", "--shm-size", "2gb",
            "--security-opt", "seccomp=unconfined",
            "--memory", "2g", "--cpus", "1.0",
            "--pids-limit", "200",
            "--tmpfs", "/tmp:rw,size=256m",
            "--name", f"worker-{task_id}",
            ECR_IMAGE,
            url_to_analyze, callback_url, task_id
        ]

        # website_id가 존재하면 추가
        if website_id:
            command.append(website_id)

        logging.info(f"[{task_id}] Running docker command: {' '.join(command)}")

        with open(log_path, "w") as logf:
            subprocess.Popen(
                command,
                stdout=logf,
                stderr=subprocess.STDOUT,
                start_new_session=True
            )

        logging.info(f"[{task_id}] Worker started (logging to {log_path})")

        return jsonify({
            "message": "Worker started",
            "task_id": task_id,
            "status": "processing"
        }), 202

    except Exception as e:
        logging.error(f"Error during analyze_request: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

# =====================================================
# Docker 로그 조회 API
# =====================================================
@app.route("/logs/<task_id>", methods=["GET"])
def get_log(task_id):
    log_path = os.path.join(LOG_DIR, f"{task_id}.log")
    if not os.path.exists(log_path):
        return jsonify({"error": "No log found"}), 404
    with open(log_path, "r") as f:
        return f.read(), 200, {"Content-Type": "text/plain"}

# =====================================================
# Worker 콜백 결과 저장 API
# =====================================================
@app.route("/result", methods=["POST"])
def save_callback():
    if not request.is_json:
        return jsonify({"error": "Unsupported Media Type, use application/json"}), 415
    try:
        data = request.get_json()
        task_id = data.get("task_id")
        if not task_id:
            return jsonify({"error": "Missing task_id in payload"}), 400
        result_path = os.path.join(RESULT_DIR, f"{task_id}.json")
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logging.info(f"[{task_id}] Callback result saved to {result_path}")
        return jsonify({"ok": True}), 200
    except Exception as e:
        logging.error(f"Error saving callback: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

# =====================================================
# Postman 조회용 API
# =====================================================
@app.route("/results/<task_id>", methods=["GET"])
def get_result(task_id):
    result_path = os.path.join(RESULT_DIR, f"{task_id}.json")
    if not os.path.exists(result_path):
        return jsonify({"error": "Result not found"}), 404
    with open(result_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)

# =====================================================
# Flask 실행
# =====================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    logging.info(f"🚀 Flask App running on port {port}")
    app.run(host="0.0.0.0", port=port)