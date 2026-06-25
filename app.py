import os
import uuid
import threading
import json
import subprocess
from flask import Flask, render_template, request, jsonify, send_file, abort

app = Flask(__name__)
DOWNLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'downloads')
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

download_jobs = {}


def human_size(n_bytes):
    if not n_bytes:
        return 'Unknown'
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
        import yt_dlp
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extractor_args': {'youtube': {'player_client': ['tv_embedded']}},
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        title = info.get('title', 'Unknown')
        author = info.get('uploader', 'Unknown')
        duration_sec = info.get('duration', 0) or 0
        thumbnail = info.get('thumbnail', '')
        view_count = info.get('view_count', 0)

        minutes, seconds = divmod(int(duration_sec), 60)
        duration = f"{minutes}:{seconds:02d}"
        views = f"{view_count:,}" if view_count else 'N/A'

        formats = info.get('formats', [])
        streams = []

        for f in formats:
            fid = f.get('format_id', '')
            vcodec = f.get('vcodec', 'none')
            acodec = f.get('acodec', 'none')
            has_video = vcodec and vcodec != 'none'
            has_audio = acodec and acodec != 'none'

            if not has_video and not has_audio:
                continue

            ext = f.get('ext', '?')
            filesize = f.get('filesize') or f.get('filesize_approx')
            resolution = f.get('resolution') or (f'{f["width"]}x{f["height"]}' if f.get('width') and f.get('height') else None)
            abr = f.get('abr')
            vbr = f.get('vbr')
            note = f.get('format_note', '')

            if has_video and has_audio:
                stream_type = 'progressive'
            elif has_video:
                stream_type = 'video'
            else:
                stream_type = 'audio'

            streams.append({
                'format_id': fid,
                'type': stream_type,
                'ext': ext,
                'resolution': resolution,
                'abr': f"{abr:.0f}kbps" if abr else None,
                'vbr': f"{vbr:.0f}kbps" if vbr else None,
                'filesize': human_size(filesize),
                'note': note,
                'has_video': has_video,
                'has_audio': has_audio,
                'vcodec': vcodec if has_video else None,
                'acodec': acodec if has_audio else None,
            })

        streams.sort(key=lambda x: (
            0 if x['type'] == 'progressive' else (1 if x['type'] == 'video' else 2),
        ))

        mp3_option = {
            'format_id': '__mp3__',
            'type': 'audio',
            'ext': 'mp3',
            'resolution': None,
            'abr': '128kbps',
            'vbr': None,
            'filesize': '~varies',
            'note': 'Best audio → MP3',
            'has_video': False,
            'has_audio': True,
            'vcodec': None,
            'acodec': 'mp3',
        }

        return jsonify({
            'title': title,
            'author': author,
            'duration': duration,
            'thumbnail': thumbnail,
            'views': views,
            'streams': [mp3_option] + streams,
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/download', methods=['POST'])
def start_download():
    data = request.get_json()
    url = (data or {}).get('url', '').strip()
    format_id = (data or {}).get('format_id', '').strip()

    if not url or not format_id:
        return jsonify({'error': 'URL and format required'}), 400

    job_id = str(uuid.uuid4())
    out_dir = os.path.join(DOWNLOAD_FOLDER, job_id)
    os.makedirs(out_dir, exist_ok=True)

    download_jobs[job_id] = {
        'status': 'starting',
        'percent': 0,
        'filename': None,
        'filepath': None,
        'error': None,
    }

    def run():
        import yt_dlp

        def progress_hook(d):
            if d['status'] == 'downloading':
                pct_str = d.get('_percent_str', '0%').strip().replace('%', '')
                try:
                    pct = float(pct_str)
                except ValueError:
                    pct = 0
                download_jobs[job_id]['percent'] = int(pct)
                download_jobs[job_id]['status'] = 'downloading'
            elif d['status'] == 'finished':
                download_jobs[job_id]['percent'] = 99
                download_jobs[job_id]['status'] = 'processing'

        try:
            common_opts = {
                'outtmpl': os.path.join(out_dir, '%(title)s.%(ext)s'),
                'progress_hooks': [progress_hook],
                'quiet': True,
                'no_warnings': True,
                'extractor_args': {'youtube': {'player_client': ['tv_embedded']}},
            }

            if format_id == '__mp3__':
                ydl_opts = {
                    **common_opts,
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                }
            else:
                ydl_opts = {
                    **common_opts,
                    'format': format_id,
                }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            files = os.listdir(out_dir)
            if files:
                fname = files[0]
                fpath = os.path.join(out_dir, fname)
                download_jobs[job_id]['status'] = 'done'
                download_jobs[job_id]['percent'] = 100
                download_jobs[job_id]['filename'] = fname
                download_jobs[job_id]['filepath'] = fpath
            else:
                download_jobs[job_id]['status'] = 'error'
                download_jobs[job_id]['error'] = 'Download failed: no output file found'

        except Exception as e:
            download_jobs[job_id]['status'] = 'error'
            download_jobs[job_id]['error'] = str(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()

    return jsonify({'job_id': job_id})


@app.route('/api/progress/<job_id>')
def progress(job_id):
    info = download_jobs.get(job_id)
    if not info:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(info)


@app.route('/api/file/<job_id>')
def serve_file(job_id):
    info = download_jobs.get(job_id)
    if not info or info.get('status') != 'done':
        abort(404)
    filepath = info.get('filepath')
    if not filepath or not os.path.exists(filepath):
        abort(404)
    return send_file(filepath, as_attachment=True, download_name=info['filename'])


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
