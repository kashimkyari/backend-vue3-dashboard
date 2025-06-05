from config import create_app
from models import Stream
from monitoring import start_monitoring
app = create_app()
with app.app_context():
    stream = Stream.query.get(<stream_id>)
    start_monitoring(stream)