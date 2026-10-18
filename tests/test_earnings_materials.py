"""法說會簡報解析（earnings_materials）——HTML 結構取自 2026-10 公開資訊觀測站實測回應，不連網。"""
import earnings_materials as em


def _row(date, summary, zh, en, video=""):
    dl = 'onclick=\'document.fm_fileDownload.fileName.value="{0}";document.fm_fileDownload.submit();\''
    v = f'影音資訊網址：<a href="{video}" target="_blank"><u>{video}</u></a><br/>' if video else ""
    return (f'<tr class="even" data-type="body"><td>3037</td><td>欣興</td><td>{date}</td><td>14:00</td>'
            f'<td>台北</td><td>{summary}</td>'
            f'<td><a href="#" {dl.format(zh)}><u>{zh}</u></a></td>'
            f'<td><a href="#" {dl.format(en)}><u>{en}</u></a></td>'
            f'<td><a href="http://www.unimicron.com/">http://www.unimicron.com/</a></td><td>{v}</td>'
            f'<td>無。</td><td><input type="button" value="查詢"/></td></tr>')


_HTML = "<table><tr><th>公司代號</th><th>公司名稱</th></tr>" + "".join([
    _row("115/07/29", "將於說明會當天發布本公司115年度第2季財務\r\n暨營運報告。",
         "303720260730M001.pdf", "303720260730E001.pdf", "https://www.unimicron.com/money03.html"),
    _row("115/09/08", "本公司受邀參加宏遠證券舉辦之2026年趨勢產業論壇",
         "303720260730M001.pdf", "303720260730E001.pdf", "http://irconference.twse.com.tw/3037_293_20260908_ch.mp4"),
    _row("115/09/16", "本公司受邀參加永豐證券舉辦之2026 Q3產業投資論壇",
         "303720260730M001.pdf", "303720260730E001.pdf"),
    _row("115/02/25", "將於說明會當天發布本公司114年度第4季財務暨營運報告。",
         "303720260225M001.pdf", "303720260225E001.pdf"),
    _row("114/11/17 至 114/11/18", "本公司受邀參加J.P. Morgan舉辦之Conference",
         "303720251110M001.pdf", "303720251110E001.pdf"),
]) + "</table>"


def test_parse_conferences_real_layout():
    rows = em.parse_conferences(_HTML, "3037")
    assert [r["日期"] for r in rows] == ["2026-09-16", "2026-09-08", "2026-07-29", "2026-02-25", "2025-11-17"]
    r = rows[2]
    assert r["自辦"] is True and r["中文簡報"] == ["303720260730M001.pdf"] and r["英文簡報"] == ["303720260730E001.pdf"]
    assert r["影音"] == ["https://www.unimicron.com/money03.html"]
    assert rows[0]["自辦"] is False and rows[0]["影音"] == []
    assert em.parse_conferences(_HTML, "2330") == []


def test_group_decks():
    decks = em.group_decks(em.parse_conferences(_HTML, "3037"))
    assert [d["簡報檔"] for d in decks] == ["303720260730M001.pdf", "303720260225M001.pdf", "303720251110M001.pdf"]
    d = decks[0]
    assert d["首次發表日"] == "2026-07-29" and d["自辦說明會"] is True and d["沿用場次"] == 3
    assert d["最近一次使用"] == "2026-09-16"
    assert d["影音"] == ["https://www.unimicron.com/money03.html",
                         "http://irconference.twse.com.tw/3037_293_20260908_ch.mp4"]
    assert decks[2]["自辦說明會"] is False and decks[2]["首次發表日"] == "2025-11-17"   # 沒有自辦場次 → 取最早一場


def test_pdf_urls_nas_fallback():
    urls = em._pdf_urls("303720260730M001.pdf", [])
    assert urls == ["https://mopsov.twse.com.tw/nas/STR/303720260730M001.pdf",
                    "https://mops.twse.com.tw/nas/STR/303720260730M001.pdf"]
