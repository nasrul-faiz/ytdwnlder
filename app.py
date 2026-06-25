import os
import uuid
import threading
import json
from flask import Flask, render_template, request, jsonify, send_file, abort
from pytube import YouTube
from pytube.exceptions import RegexMatchError, VideoUnavailable

app = Flask(__name__)
app.config['DOWNLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'downloads')
os.makedirs(app.config['DOWNLOAD_FOLDER'], exist_ok=True)

download_progress = {}


def human_size(n_bytes):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/info', methods=['POST'])
def get_info():
    data = request.get_json()
    url = (data or {}).get('url', '').strip()
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    try:
        yt = YouTube(url)
        title = yt.title
        author = yt.author
        length = yt.length
        thumbnail = yt.thumbnail_url
        views = yt.views

        streams = []
        for s in yt.streams:
            entry = {
                'itag': s.itag,
                'mime_type': s.mime_type,
                'type': s.type,
                'progressive': s.is_progressive,
                'resolution': s.resolution if s.type == 'video' else None,
                'abr': s.abr if s.type == 'audio' else None,
                'filesize': human_size(s.filesize) if s.filesize else 'Unknown',
                'codecs': s.codecs,
                'subtype': s.subtype,
            }
            streams.append(entry)

        streams.sort(key=lambda x: (
            0 if x['progressive'] else (1 if x['type'] == 'video' else 2),
            -(int(x['resolution'][:-1]) if x['resolution'] and x['resolution'][:-1].isdigit() else 0)
        ))

        minutes, seconds = divmod(length or 0, 60)
        duration = f"{minutes}:{seconds:02d}"

        return jsonify({
            'title': title,
            'author': author,
            'duration': duration,
            'thumbnail': thumbnail,
            'views': f"{views:,}" if views else 'N/A',
            'streams': streams,
        })
    except RegexMatchError:
        return jsonify({'error': 'Invalid YouTube URL'}), 400
    except VideoUnavailable:
        return jsonify({'error': 'Video is unavailable or private'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/download', methods=['POST'])
def start_download():
    data = request.get_json()
    url = (data or {}).get('url', '').strip()
    itag = (data or {}).get('itag')

    if not url or not itag:
        return jsonify({'error': 'URL and stream selection required'}), 400

    job_id = str(uuid.uuid4())
    download_progress[job_id] = {'status': 'starting', 'percent': 0, 'filename': None, 'error': None}

    def run():
        try:
            def on_progress(stream, chunk, remaining):
                total = stream.filesize
                downloaded = total - remaining
                pct = int(downloaded / total * 100) if total else 0
                download_progress[job_id]['percent'] = pct
                download_progress[job_id]['status'] = 'downloading'

            def on_complete(stream, path):
                download_progress[job_id]['status'] = 'done'
                download_progress[job_id]['percent'] = 100
                download_progress[job_id]['filename'] = os.path.basename(path)
                download_progress[job_id]['filepath'] = path

            yt = YouTube(url, on_progress_callback=on_progress, on_complete_callback=on_complete)
            stream = yt.streams.get_by_itag(int(itag))
            if not stream:
                download_progress[job_id]['status'] = 'error'
                download_progress[job_id]['error'] = 'Stream not found'
                return
            out_dir = os.path.join(app.config['DOWNLOAD_FOLDER'], job_id)
            os.makedirs(out_dir, exist_ok=True)
            stream.download(output_path=out_dir)
        except Exception as e:
            download_progress[job_id]['status'] = 'error'
            download_progress[job_id]['error'] = str(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()

    return jsonify({'job_id': job_id})


@app.route('/api/progress/<job_id>')
def progress(job_id):
    info = download_progress.get(job_id)
    if not info:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(info)


@app.route('/api/file/<job_id>')
def serve_file(job_id):
    info = download_progress.get(job_id)
    if not info or info.get('status') != 'done':
        abort(404)
    filepath = info.get('filepath')
    if not filepath or not os.path.exists(filepath):
        abort(404)
    return send_file(filepath, as_attachment=True, download_name=info['filename'])


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
