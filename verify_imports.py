modules = [
    'google.genai', 'dotenv', 'RealtimeSTT', 'elevenlabs', 'PySide6',
    'cv2', 'PIL', 'mss', 'websockets', 'numpy', 'pyaudio'
]
ok = True
for m in modules:
    try:
        __import__(m)
        print(f"OK  {m}")
    except Exception as e:
        ok = False
        print(f"ERR {m}: {e}")
print("ALL_IMPORTS_OK" if ok else "IMPORTS_FAILED")
