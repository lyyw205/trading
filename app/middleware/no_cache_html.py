from __future__ import annotations


class NoCacheHTMLMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                raw_headers = message.get("headers", [])
                content_type = b""
                for k, v in raw_headers:
                    if k == b"content-type":
                        content_type = v
                        break
                if b"text/html" in content_type:
                    extra = [
                        (b"cache-control", b"no-cache, no-store, must-revalidate"),
                        (b"pragma", b"no-cache"),
                        (b"expires", b"0"),
                    ]
                    message["headers"] = list(raw_headers) + extra
            await send(message)

        await self.app(scope, receive, send_wrapper)
