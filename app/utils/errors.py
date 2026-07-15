class GatewayError(Exception):
    """统一业务异常，最终会被 main.py 转成统一 JSON。"""

    def __init__(self, message: str, status_code: int = 400, detail=None):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)
