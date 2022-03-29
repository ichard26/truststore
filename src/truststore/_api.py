import os
import platform
import socket
import ssl
from typing import Any

from _ssl import ENCODING_DER  # type: ignore[import]

if platform.system() == "Windows":
    from ._windows import _configure_context, _verify_peercerts_impl
elif platform.system() == "Darwin":
    from ._macos import _configure_context, _verify_peercerts_impl
else:
    from ._openssl import _configure_context, _verify_peercerts_impl


_CERTIFI_WHERE: str | None
try:
    from certifi import where

    _CERTIFI_WHERE = where()
except ImportError:
    _CERTIFI_WHERE = None


class TruststoreSSLContext(ssl.SSLContext):
    """SSLContext API that uses system certificates on all platforms"""

    def __init__(self, protocol: int = ssl.PROTOCOL_TLS) -> None:
        self._ctx = ssl.SSLContext(protocol)
        _configure_context(self._ctx)

        class TruststoreSSLObject(ssl.SSLObject):
            # This object exists because wrap_bio() doesn't
            # immediately do the handshake so we need to do
            # certificate verifications after SSLObject.do_handshake()
            _TRUSTSTORE_SERVER_HOSTNAME = None

            def do_handshake(self) -> None:
                ret = super().do_handshake()
                _verify_peercerts(
                    self, server_hostname=self._TRUSTSTORE_SERVER_HOSTNAME
                )
                return ret

        self._ctx.sslobject_class = TruststoreSSLObject  # type: ignore[misc]

    def wrap_socket(
        self,
        sock: socket.socket,
        server_side: bool = False,
        do_handshake_on_connect: bool = True,
        suppress_ragged_eofs: bool = True,
        server_hostname: str | None = None,
        session: ssl.SSLSession | None = None,
    ) -> ssl.SSLSocket:
        ssl_sock = self._ctx.wrap_socket(
            sock,
            server_side=server_side,
            server_hostname=server_hostname,
            do_handshake_on_connect=do_handshake_on_connect,
            suppress_ragged_eofs=suppress_ragged_eofs,
            session=session,
        )
        _verify_peercerts(ssl_sock, server_hostname=server_hostname)
        return ssl_sock

    def wrap_bio(
        self,
        incoming: ssl.MemoryBIO,
        outgoing: ssl.MemoryBIO,
        server_side: bool = False,
        server_hostname: str | None = None,
        session: ssl.SSLSession | None = None,
    ) -> ssl.SSLObject:
        # Super hacky way of passing the server_hostname value forward to sslobject_class.
        self._ctx.sslobject_class._TRUSTSTORE_SERVER_HOSTNAME = server_hostname  # type: ignore[attr-defined]
        ssl_obj = self._ctx.wrap_bio(
            incoming,
            outgoing,
            server_hostname=server_hostname,
            server_side=server_side,
            session=session,
        )
        return ssl_obj

    def load_verify_locations(
        self,
        cafile: str | bytes | os.PathLike[str] | os.PathLike[bytes] | None = None,
        capath: str | bytes | os.PathLike[str] | os.PathLike[bytes] | None = None,
        cadata: str | bytes | None = None,
    ) -> None:
        # Ignore certifi.where() being used as a default, otherwise we raise an error.
        if (
            _CERTIFI_WHERE
            and not cadata
            and (cafile is None or cafile == _CERTIFI_WHERE)
            and (capath is None or capath == _CERTIFI_WHERE)
        ):
            return
        raise NotImplementedError(
            "TruststoreSSLContext.load_verify_locations() isn't implemented"
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)


def _verify_peercerts(
    sock_or_sslobj: ssl.SSLSocket | ssl.SSLObject, server_hostname: str | None
) -> None:
    """
    Verifies the peer certificates from an SSLSocket or SSLObject
    against the certificates in the OS trust store.
    """
    sslobj: ssl.SSLObject = sock_or_sslobj  # type: ignore[assignment]
    try:
        while not hasattr(sslobj, "get_unverified_chain"):
            sslobj = sslobj._sslobj  # type: ignore[attr-defined]
    except AttributeError:
        pass

    cert_bytes = [
        cert.public_bytes(ENCODING_DER) for cert in sslobj.get_unverified_chain()  # type: ignore[attr-defined]
    ]
    _verify_peercerts_impl(cert_bytes, server_hostname=server_hostname)
