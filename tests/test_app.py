from unittest import mock

import app


def test_build_ydl_opts_defaults(monkeypatch):
    monkeypatch.delenv('YTDLP_PLAYER_CLIENTS', raising=False)
    monkeypatch.delenv('YTDLP_COOKIEFILE', raising=False)
    monkeypatch.delenv('YTDLP_COOKIES_FROM_BROWSER', raising=False)

    opts = app.build_ydl_opts(skip_download=True)

    assert opts['skip_download'] is True
    assert opts['extractor_args']['youtube']['player_client'] == [
        'tv_embedded', 'android', 'ios', 'web'
    ]
    assert 'cookiefile' not in opts
    assert 'cookiesfrombrowser' not in opts


def test_build_ydl_opts_uses_cookie_configuration(monkeypatch):
    monkeypatch.setenv('YTDLP_PLAYER_CLIENTS', 'ios, web')
    monkeypatch.setenv('YTDLP_COOKIEFILE', '/tmp/cookies.txt')
    monkeypatch.setenv('YTDLP_COOKIES_FROM_BROWSER', 'firefox:default::Meta')

    opts = app.build_ydl_opts(format_id='18')

    assert opts['format'] == '18'
    assert opts['extractor_args']['youtube']['player_client'] == ['ios', 'web']
    assert opts['cookiefile'] == '/tmp/cookies.txt'
    assert opts['cookiesfrombrowser'] == ('firefox', 'default', '', 'Meta')


def test_get_info_rewrites_bot_error(monkeypatch):
    class FakeYoutubeDL:
        def __init__(self, params):
            self.params = params

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            raise Exception("Sign in to confirm you're not a bot")

    monkeypatch.delenv('YTDLP_COOKIEFILE', raising=False)
    monkeypatch.delenv('YTDLP_COOKIES_FROM_BROWSER', raising=False)

    with mock.patch('yt_dlp.YoutubeDL', FakeYoutubeDL):
        client = app.app.test_client()
        response = client.post('/api/info', json={'url': 'https://youtu.be/test'})

    assert response.status_code == 500
    assert response.get_json() == {
        'error': (
            'YouTube is asking for authentication for this video. '
            'Set YTDLP_COOKIEFILE to an exported cookies.txt file or '
            'YTDLP_COOKIES_FROM_BROWSER to a browser name such as chrome, firefox, or edge '
            'on the server running this app.'
        )
    }


def test_get_info_omits_mp3_when_ffmpeg_missing(monkeypatch):
    class FakeYoutubeDL:
        def __init__(self, params):
            self.params = params

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            return {
                'title': 'Test',
                'uploader': 'Uploader',
                'duration': 90,
                'thumbnail': 'thumb.jpg',
                'view_count': 123,
                'formats': [{
                    'format_id': '140',
                    'ext': 'm4a',
                    'acodec': 'mp4a.40.2',
                    'vcodec': 'none',
                    'abr': 128,
                }],
            }

    monkeypatch.setattr(app, 'ffmpeg_available', lambda: False)

    with mock.patch('yt_dlp.YoutubeDL', FakeYoutubeDL):
        client = app.app.test_client()
        response = client.post('/api/info', json={'url': 'https://youtu.be/test'})

    assert response.status_code == 200
    payload = response.get_json()
    assert [stream['format_id'] for stream in payload['streams']] == ['140']


def test_start_download_rejects_mp3_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(app, 'ffmpeg_available', lambda: False)

    client = app.app.test_client()
    response = client.post('/api/download', json={
        'url': 'https://youtu.be/test',
        'format_id': '__mp3__',
    })

    assert response.status_code == 400
    assert response.get_json() == {
        'error': 'MP3 conversion is unavailable on this server because ffmpeg and ffprobe are not installed.'
    }