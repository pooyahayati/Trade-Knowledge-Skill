"""Public data boundary regression checks; entirely offline."""
import importlib.util,tempfile,unittest,urllib.request
from pathlib import Path
from unittest.mock import patch

path=Path(__file__).resolve().parent/'expanded_market/fetch.py'
spec=importlib.util.spec_from_file_location('public_fetch',path)
F=importlib.util.module_from_spec(spec);spec.loader.exec_module(F)
URL='https://data.binance.vision/data/example.zip'

class PublicDownloadTests(unittest.TestCase):
    def test_redirect_is_rejected_before_followup_request(self):
        handler=F.ProviderRedirects();request=urllib.request.Request(URL)
        for target in ['http://data.binance.vision/file','https://127.0.0.1/file','https://example.com/file',
                       'https://user:password@data.binance.vision/file','https://data.binance.vision:444/file']:
            with self.subTest(target=target),self.assertRaises(ValueError):
                handler.redirect_request(request,None,302,'Found',{},target)
        safe=handler.redirect_request(request,None,302,'Found',{},URL+'2')
        self.assertEqual(safe.full_url,URL+'2')

    def test_orphan_raw_and_receipt_are_never_overwritten(self):
        for raw in [True,False]:
            with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
                base=Path(directory).resolve()
                self.assertEqual(base.parent,Path(__file__).resolve().parent)
                p=base/('x.zip' if raw else 'x.zip.receipt.json')
                p.write_bytes(b'original evidence')
                with patch.object(F,'RAW',base),patch.object(F,'fetch') as download,self.assertRaises(ValueError):
                    F.job(('x.zip',URL,True))
                download.assert_not_called();self.assertEqual(p.read_bytes(),b'original evidence')

    def test_oversized_body_rejected_without_persistence(self):
        class Response:
            url=URL;headers={}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return b'x'*n
        class Opener:
            def open(self,*args,**kwargs):return Response()
        with patch.object(F.urllib.request,'build_opener',return_value=Opener()),patch.object(F,'MAX_BYTES',100),self.assertRaises(ValueError):
            F.fetch(URL)

if __name__=='__main__':unittest.main()
