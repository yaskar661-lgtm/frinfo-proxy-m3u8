from flask import Flask, Response, request
import re
import requests
import urllib3
from urllib.parse import urlparse, parse_qs, unquote, quote

app = Flask(__name__)

# SSL uyarılarını gizle
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TARGET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.france.tv/",
    "Origin": "https://www.france.tv/"
}

# Taze Location / yönlendirme sağlayan kaynak
ORIGINAL_MASTER_URL = "https://tvradiozap.eu/tools/ft-m3u8.php/franceinfo.m3u8"

def get_fresh_master_url():
    """
    tvradiozap.eu veya benzeri kaynaktan güncel Location header'ını veya 
    taze m3u8 adresini alır.
    """
    try:
        resp = requests.get(ORIGINAL_MASTER_URL, headers=TARGET_HEADERS, allow_redirects=True, timeout=10, verify=False)
        # Eğer doğrudan son m3u8 içeriği dönüyorsa veya yönlendirme verdiyse url'yi döndür
        return resp.url if resp.status_code == 200 else ORIGINAL_MASTER_URL
    except Exception:
        return ORIGINAL_MASTER_URL

def clean_and_proxy_url(raw_url, host_root):
    """
    URL'leri temizler ve Flask proxy adresimize bağlar. Boşluk ve imza bozulmalarını önler.
    """
    clean_target = raw_url
    while "url=" in clean_target:
        parsed = urlparse(clean_target)
        query_params = parse_qs(parsed.query)
        if "url" in query_params:
            clean_target = query_params["url"][0]
        else:
            break

    proxy_prefix = f"{host_root}/proxy?url="
    if raw_url.startswith(proxy_prefix):
        return raw_url

    return f"{proxy_prefix}{quote(clean_target, safe=':/?=&%-_.~')}"

@app.route("/master.m3u8")
def proxy_master():
    try:
        # Taze adresi alıyoruz
        target_url = get_fresh_master_url()
        
        resp = requests.get(
            target_url,
            headers=TARGET_HEADERS,
            timeout=10,
            verify=False,
        )
        resp.raise_for_status()

        playlist_content = resp.text
        base_url = re.sub(r"[^/]+$", "", target_url)
        host_root = request.host_url.rstrip("/")

        processed_lines = []
        for line in playlist_content.splitlines():
            line = line.strip()
            if line:
                if "URI=" in line or "URL=" in line:
                    def fix_url(match):
                        prefix = match.group(1)
                        val = match.group(2)
                        quote_char = match.group(3)

                        if not val.startswith(("http://", "https://")):
                            val = base_url + val

                        val = clean_and_proxy_url(val, host_root)
                        return f"{prefix}{val}{quote_char}"

                    line = re.sub(r'((?:URI|URL)=["\']?)([^"\']*)(["\']?)', fix_url, line)

                elif not line.startswith("#"):
                    if not line.startswith(("http://", "https://")):
                        line = base_url + line
                    
                    line = clean_and_proxy_url(line, host_root)

            processed_lines.append(line)
          
        updated_playlist = "\n".join(processed_lines)
        return Response(updated_playlist, mimetype="application/vnd.apple.mpegurl")

    except requests.exceptions.RequestException as e:
        return f"Master playlist alınırken hata oluştu: {str(e)}", 500
    except Exception as e:
        return str(e), 500


@app.route("/proxy")
def proxy_media():
    target_url = request.args.get("url")
    if not target_url:
        return "URL belirtilmedi", 400

    try:
        # URL kodlamasını düzgün çöz ama imza parametrelerinin bütünlüğünü koru
        target_url = unquote(target_url)

        req = requests.get(
            target_url,
            headers=TARGET_HEADERS,
            stream=True,
            timeout=10,
            verify=False,
        )
        req.raise_for_status()

        content_type = req.headers.get("Content-Type", "application/octet-stream")

        if (
            "application/vnd.apple.mpegurl" in content_type
            or "audio/mpegurl" in content_type
            or target_url.endswith(".m3u8")
        ):
            playlist_content = req.text
            base_url = re.sub(r"[^/]+$", "", target_url)
            host_root = request.host_url.rstrip("/")

            new_lines = []
            for line in playlist_content.splitlines():
                line = line.strip()
                if line:
                    if line.startswith("#"):
                        if "URI=" in line:
                            def fix_sub_url(match):
                                prefix = match.group(1)
                                val = match.group(2)
                                quote_char = match.group(3)
                                if not val.startswith(("http://", "https://")):
                                    val = base_url + val
                                val = clean_and_proxy_url(val, host_root)
                                return f"{prefix}{val}{quote_char}"
                            line = re.sub(r'((?:URI|URL)=["\']?)([^"\']*)(["\']?)', fix_sub_url, line)
                        
                        new_lines.append(line)
                    else:
                        if not line.startswith(("http://", "https://")):
                            segment_full_url = base_url + line
                        else:
                            segment_full_url = line

                        proxied_segment_url = clean_and_proxy_url(segment_full_url, host_root)
                        new_lines.append(proxied_segment_url)
                else:
                    new_lines.append(line)

            updated_playlist = "\n".join(new_lines)
            return Response(updated_playlist, mimetype="application/vnd.apple.mpegurl")

        return Response(
            req.iter_content(chunk_size=131072),
            status=req.status_code,
            mimetype=content_type,
        )

    except requests.exceptions.RequestException as e:
        return f"Medya/Parça alınırken hata oluştu: {str(e)}", 500
    except Exception as e:
        return str(e), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
