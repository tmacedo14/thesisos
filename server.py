from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = 3000

server = ThreadingHTTPServer((HOST, PORT), SimpleHTTPRequestHandler)

print(f"ThesisOS disponível na porta {PORT}")

try:
    server.serve_forever()
except KeyboardInterrupt:
    server.server_close()