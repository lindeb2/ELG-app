import http.server
import socketserver
import webbrowser
import os

PORT = 8000
DIRECTORY = r"C:\Users\Johan\Desktop\Timetable\Project Folder"

os.chdir(DIRECTORY)

Handler = http.server.SimpleHTTPRequestHandler

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Server running at http://localhost:{PORT}")
    print(f"Opening callgraph.html in browser...")
    webbrowser.open(f"http://localhost:{PORT}/callgraph.html")
    print("Press Ctrl+C to stop the server")
    httpd.serve_forever()