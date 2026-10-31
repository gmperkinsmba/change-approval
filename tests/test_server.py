import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from rba.server import make_handler


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.server=ThreadingHTTPServer(("127.0.0.1",0),make_handler(Path(self.temp.name),"test-session-token"))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2);self.temp.cleanup()

    def get(self,path):
        with urlopen(self.base+path,timeout=3) as response:return response.status,response.read(),response.headers

    def post(self,path,data,headers=None):
        body=json.dumps(data).encode()
        request=Request(self.base+path,data=body,headers={"Content-Type":"application/json",**(headers or {})},method="POST")
        try:
            with urlopen(request,timeout=3) as response:return response.status,json.loads(response.read())
        except HTTPError as error:
            with error:return error.code,json.loads(error.read())

    def test_token_origin_policy_and_stale_plan_execution(self):
        status, body, headers=self.get("/")
        self.assertEqual(status,200)
        self.assertIn(b"<h1>Review Before Action</h1>",body)
        self.assertIn("default-src 'self'",headers["Content-Security-Policy"])
        denied,_=self.post("/api/plan",{"target_id":"DOC-001","payload":"new"})
        self.assertEqual(denied,403)
        denied_origin,_=self.post("/api/plan",{"target_id":"DOC-001","payload":"new"},{"X-RBA-Token":"test-session-token","Origin":"http://127.0.0.1:1234"})
        self.assertEqual(denied_origin,403)
        allowed={"X-RBA-Token":"test-session-token"}
        status,plan=self.post("/api/plan",{"target_id":"DOC-001","payload":"Reviewed v1"},allowed)
        self.assertEqual(status,201)
        plan=plan["plan"]
        status,approval=self.post("/api/approve",{"plan_id":plan["plan_id"],"version":plan["version"],"fingerprint":plan["fingerprint"]},allowed)
        self.assertEqual(status,201)
        status,changed=self.post("/api/plan/revise",{"plan_id":plan["plan_id"],"version":1,"target_id":"DOC-001","payload":"Changed v2"},allowed)
        self.assertEqual(status,200)
        status,rejected=self.post("/api/execute",{"approval_id":approval["approval"]["approval_id"]},allowed)
        self.assertEqual(status,409)
        self.assertEqual(rejected["receipt"]["status"],"rejected")
        fresh=changed["plan"]
        _,approved=self.post("/api/approve",{"plan_id":fresh["plan_id"],"version":fresh["version"],"fingerprint":fresh["fingerprint"]},allowed)
        status,applied=self.post("/api/execute",{"approval_id":approved["approval"]["approval_id"]},allowed)
        self.assertEqual(status,200)
        self.assertEqual(applied["receipt"]["status"],"verified")

    def test_settings_permissions_apply_at_api_boundary_and_body_is_bounded(self):
        headers={"X-RBA-Token":"test-session-token"}
        _,settings=self.post("/api/settings",{"can_prepare":False},headers)
        self.assertFalse(settings["can_prepare"])
        status,data=self.post("/api/plan",{"target_id":"DOC-001","payload":"no"},headers)
        self.assertEqual(status,403)
        oversized={"target_id":"DOC-001","payload":"x"*100_001}
        status,data=self.post("/api/plan",oversized,headers)
        self.assertEqual(status,400)
        self.assertIn("100 KB",data["error"])


if __name__=="__main__":unittest.main()
