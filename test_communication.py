import requests
import json

def test_communication():
    """Test communication between main and monitor apps."""
    
    # Test main app health
    try:
        response = requests.get('https://localhost:5000/api/health', verify=False, timeout=10)
        print(f"Main app health: {response.status_code} - {response.json()}")
    except Exception as e:
        print(f"Main app unreachable: {e}")
    
    # Test monitor app health
    try:
        response = requests.get('https://localhost:5001/api/monitor/health', verify=False, timeout=10)
        print(f"Monitor app health: {response.status_code} - {response.json()}")
    except Exception as e:
        print(f"Monitor app unreachable: {e}")
    
    # Test communication from main to monitor
    try:
        response = requests.get('https://localhost:5000/api/health/monitor', verify=False, timeout=10)
        print(f"Main->Monitor communication: {response.status_code} - {response.json()}")
    except Exception as e:
        print(f"Main->Monitor communication failed: {e}")

if __name__ == "__main__":
    test_communication()