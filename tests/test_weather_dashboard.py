from fastapi.testclient import TestClient
from weather_dashboard import app

test_client = TestClient(app)


def test_read_root():
    response = test_client.get('/')
    assert response.status_code == 200
    assert response.json() == {'message': 'Welcome to the Weather Dashboard'}