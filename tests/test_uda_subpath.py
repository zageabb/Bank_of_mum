"""UDA mounting test using isolated, empty loan data."""
import app as bank

def test_uda_and_lan(tmp_path, monkeypatch):
    monkeypatch.setattr(bank, "DATA_DIR", str(tmp_path))
    bank.app.config["TESTING"] = True
    client=bank.app.test_client()
    local=client.get("/")
    assert local.status_code==200
    assert '<base href="/">' in local.get_data(as_text=True)
    headers={"X-Forwarded-Prefix":"/apps/bank-of-mum",
             "X-Forwarded-Host":"tanyaanne.ddns.net",
             "X-Forwarded-Proto":"https"}
    proxied=client.get("/",headers=headers)
    assert proxied.status_code==200
    html=proxied.get_data(as_text=True)
    assert '<base href="/apps/bank-of-mum/">' in html
    assert '/apps/bank-of-mum/static/style.css' in html
    assert '/apps/bank-of-mum/loan/add' in html
