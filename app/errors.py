"""應用程式共用例外。"""


class AppError(Exception):
    """帶有英文錯誤代碼與繁中使用者訊息的例外。

    code 給程式判斷與 API 回傳用；message 直接顯示給使用者。
    """

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
