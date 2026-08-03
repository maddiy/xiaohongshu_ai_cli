"""供XHSClient拆分模块延迟访问兼容门面，避免循环导入。"""


class _XHSClientProxy:
    def __getattr__(self, name):
        from .xhs_client import XHSClient as client_class

        return getattr(client_class, name)


XHSClient = _XHSClientProxy()
