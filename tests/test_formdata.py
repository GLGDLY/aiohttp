import io
import pathlib
import tarfile
from unittest import mock

import pytest

from aiohttp import FormData, web
from aiohttp.client_exceptions import ClientConnectionError
from aiohttp.http_writer import StreamWriter
from aiohttp.pytest_plugin import AiohttpClient


@pytest.fixture
def buf() -> bytearray:
    return bytearray()


@pytest.fixture
def writer(buf: bytearray) -> StreamWriter:
    writer = mock.create_autospec(StreamWriter, spec_set=True)

    async def write(chunk: bytes) -> None:
        buf.extend(chunk)

    writer.write.side_effect = write
    return writer  # type: ignore[no-any-return]


def test_formdata_multipart(buf: bytearray) -> None:
    form = FormData()
    assert not form.is_multipart

    form.add_field("test", b"test", filename="test.txt")
    assert form.is_multipart


def test_invalid_formdata_payload() -> None:
    form = FormData()
    form.add_field("test", object(), filename="test.txt")
    with pytest.raises(TypeError):
        form()


def test_invalid_formdata_params() -> None:
    with pytest.raises(TypeError):
        FormData("asdasf")


def test_invalid_formdata_params2() -> None:
    with pytest.raises(TypeError):
        FormData("as")  # 2-char str is not allowed


async def test_formdata_textio_charset(buf: bytearray, writer: StreamWriter) -> None:
    form = FormData()
    body = io.TextIOWrapper(io.BytesIO(b"\xe6\x97\xa5\xe6\x9c\xac"), encoding="utf-8")
    form.add_field("foo", body, content_type="text/plain; charset=shift-jis")
    payload = form()
    await payload.write(writer)
    assert b"charset=shift-jis" in buf
    assert b"\x93\xfa\x96{" in buf


def test_invalid_formdata_content_type() -> None:
    form = FormData()
    invalid_vals = [0, 0.1, {}, [], b"foo"]
    for invalid_val in invalid_vals:
        with pytest.raises(TypeError):
            form.add_field("foo", "bar", content_type=invalid_val)  # type: ignore[arg-type]


def test_invalid_formdata_filename() -> None:
    form = FormData()
    invalid_vals = [0, 0.1, {}, [], b"foo"]
    for invalid_val in invalid_vals:
        with pytest.raises(TypeError):
            form.add_field("foo", "bar", filename=invalid_val)  # type: ignore[arg-type]


async def test_formdata_field_name_is_quoted(
    buf: bytearray, writer: StreamWriter
) -> None:
    form = FormData(charset="ascii")
    form.add_field("email 1", "xxx@x.co", content_type="multipart/form-data")
    payload = form()
    await payload.write(writer)
    assert b'name="email\\ 1"' in buf


async def test_formdata_field_name_is_not_quoted(
    buf: bytearray, writer: StreamWriter
) -> None:
    form = FormData(quote_fields=False, charset="ascii")
    form.add_field("email 1", "xxx@x.co", content_type="multipart/form-data")
    payload = form()
    await payload.write(writer)
    assert b'name="email 1"' in buf


async def test_formdata_boundary_param() -> None:
    boundary = "some_boundary"
    form = FormData(boundary=boundary)
    assert form._writer.boundary == boundary


async def test_formdata_on_redirect(aiohttp_client: AiohttpClient) -> None:
    with pathlib.Path(pathlib.Path(__file__).parent / "sample.txt").open("rb") as fobj:
        content = fobj.read()
        fobj.seek(0)

        async def handler_0(request: web.Request):
            raise web.HTTPPermanentRedirect("/1")

        async def handler_1(request: web.Request) -> web.Response:
            req_data = await request.post()
            assert req_data["sample.txt"].file.read() == content
            return web.Response()

        app = web.Application()
        app.router.add_post("/0", handler_0)
        app.router.add_post("/1", handler_1)

        client = await aiohttp_client(app)

        data = FormData()
        data._gen_form_data = mock.Mock(wraps=data._gen_form_data)
        data.add_field("sample.txt", fobj)

        resp = await client.post("/0", data=data)
        assert len(data._writer._parts) == 1
        assert resp.status == 200

        resp.release()


async def test_formdata_on_redirect_after_recv(aiohttp_client: AiohttpClient) -> None:
    with pathlib.Path(pathlib.Path(__file__).parent / "sample.txt").open("rb") as fobj:
        content = fobj.read()
        fobj.seek(0)

        async def handler_0(request: web.Request):
            req_data = await request.post()
            assert req_data["sample.txt"].file.read() == content
            raise web.HTTPPermanentRedirect("/1")

        async def handler_1(request: web.Request) -> web.Response:
            req_data = await request.post()
            assert req_data["sample.txt"].file.read() == content
            return web.Response()

        app = web.Application()
        app.router.add_post("/0", handler_0)
        app.router.add_post("/1", handler_1)

        client = await aiohttp_client(app)

        data = FormData()
        data._gen_form_data = mock.Mock(wraps=data._gen_form_data)
        data.add_field("sample.txt", fobj)

        resp = await client.post("/0", data=data)
        assert len(data._writer._parts) == 1
        assert resp.status == 200

        resp.release()


async def test_streaming_tarfile_on_redirect(aiohttp_client: AiohttpClient) -> None:
    data = b"This is a tar file payload text file."

    async def handler_0(request: web.Request):
        await request.read()
        raise web.HTTPPermanentRedirect("/1")

    async def handler_1(request: web.Request) -> web.Response:
        await request.read()
        return web.Response()

    app = web.Application()
    app.router.add_post("/0", handler_0)
    app.router.add_post("/1", handler_1)

    client = await aiohttp_client(app)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        ti = tarfile.TarInfo(name="payload1.txt")
        ti.size = len(data)
        tf.addfile(tarinfo=ti, fileobj=io.BytesIO(data))

    # Streaming tarfile.
    buf.seek(0)
    tf = tarfile.open(fileobj=buf, mode="r|")
    for entry in tf:
        with pytest.raises(ClientConnectionError) as exc_info:
            await client.post("/0", data=tf.extractfile(entry))
        cause_exc = exc_info._excinfo[1].__cause__
        assert isinstance(cause_exc, RuntimeError)
        assert len(cause_exc.args) == 1
        assert cause_exc.args[0].startswith("Non-seekable IO payload")
