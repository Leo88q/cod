import io
import wave

from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.main import app


def test_spoofed_and_empty_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(config,'MEDIA_DIR',str(tmp_path))
    client = TestClient(app)
    for filename, data in [('fake.png',b'<script>bad</script>'),('fake.webm',b'not audio'),('empty.jpg',b'')]:
        assert client.post('/api/upload',files={'file':(filename,data)}).status_code == 400
    assert list(tmp_path.iterdir()) == []


def test_image_is_reencoded_and_metadata_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(config,'MEDIA_DIR',str(tmp_path))
    source = io.BytesIO()
    exif = Image.Exif()
    exif[270] = 'private metadata'
    Image.new('RGB',(2500,10),'red').save(source,format='JPEG',exif=exif)
    response = TestClient(app).post('/api/upload',files={'file':('image.jpg',source.getvalue())})
    assert response.status_code == 200
    filename = response.json()['path'].split('/')[-1]
    with Image.open(tmp_path / filename) as result:
        assert max(result.size) <= 2048
        assert result.getexif().get(270) is None
    assert len(list(tmp_path.iterdir())) == 1


def test_audio_is_decoded_and_reencoded(tmp_path, monkeypatch):
    monkeypatch.setattr(config,'MEDIA_DIR',str(tmp_path))
    source=io.BytesIO()
    with wave.open(source,'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b'\x00\x00'*2400)
    response=TestClient(app).post('/api/upload',files={'file':('voice.wav',source.getvalue())})
    assert response.status_code == 200, response.text
    filename=response.json()['path'].split('/')[-1]
    assert filename.endswith('.mp3')
    assert (tmp_path / filename).read_bytes().startswith(b'ID3')
    assert len(list(tmp_path.iterdir())) == 1
