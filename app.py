from flask import Flask, Response, request
import re
import requests
import os
import urllib3
from urllib.parse import urlparse, parse_qs, unquote

app = Flask(__name__)

# SSL uyarılarını gizle
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Orijinal istek için gereken header'lar
TARGET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

ORIGINAL_MASTER_URL = "https://palxlendimgaliba1010.mywire.org/proxy.php?url=https://raw.githubusercontent.com/Paradise-91/ParaTV/refs/heads/main/streams/francetv/franceinfo.m3u8"

def clean_and_proxy_url(raw_url, host_root):
    """
    Gelen herhangi bir URL'yi temizler ve bizim Flask proxy'mize uygun hale getirir.
    İç içe geçmiş proxy parametrelerini çözer (örn: proxy.php?url=... vb.).
    """
    # Eğer URL içerisinde başka bir proxy'nin 'url=' parametresi varsa en içteki gerçek adrese ulaşmaya çalışalım
    while "url=" in raw_url:
        parsed = urlparse(raw_url)
        query_params = parse_qs(parsed.query)
        if "url" in query_params:
            raw_url = query_params["url"][0]
        else:
            break

    # Eğer hala tam bir URL değilse (relative path ise) atla ya da ham bırak
    # Ancak burada hedef gerçek .ftven.fr veya benzeri bir adres olmalı.
    proxy_prefix = f"{host_root}/proxy?url="
    if raw_url.startswith(proxy_prefix):
        return raw_url
    
    return f"{proxy_prefix}{raw_url}"

@app.route("/master.m3u8")
def proxy_master():
    try:
        resp = requests.get(
            ORIGINAL_MASTER_URL,
            headers=TARGET_HEADERS,
            timeout=10,
            verify=False,
        )
        resp.raise_for_status()

        playlist_content = resp.text
        base_url = re.sub(r"[^/]+$", "", ORIGINAL_MASTER_URL)
        host_root = request.host_url.rstrip("/")

        processed_lines = []
        for line in playlist_content.splitlines():
            line = line.strip()
            if line:
                # 1. Durum: #EXT-X-MEDIA gibi etiket içindeki URI/URL parametreleri
                if "URI=" in line or "URL=" in line:
                    def fix_url(match):
                        prefix = match.group(1)
                        val = match.group(2)
                        quote = match.group(3)

                        if not val.startswith(("http://", "https://")):
                            val = base_url + val

                        val = clean_and_proxy_url(val, host_root)
                        return f"{prefix}{val}{quote}"

                    line = re.sub(r'((?:URI|URL)=["\']?)([^"\']*)(["\']?)', fix_url, line)

                # 2. Durum: Normal stream bant genişliği/çözünürlük linkleri (# ile başlamayanlar)
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
        req = requests.get(
            target_url,
            headers=TARGET_HEADERS,
            stream=True,
            timeout=10,
            verify=False,
        )
        req.raise_for_status()

        content_type = req.headers.get("Content-Type", "application/octet-stream")

        # Eğer gelen içerik bir alt m3u8 dosyası ise, içerisindeki segmentleri düzenle
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
                        # Eğer alt m3u8 içinde de URI içeren etiketler varsa (örn: altyazı veya ses akışları)
                        if "URI=" in line:
                            def fix_sub_url(match):
                                prefix = match.group(1)
                                val = match.group(2)
                                quote = match.group(3)
                                if not val.startswith(("http://", "https://")):
                                    val = base_url + val
                                val = clean_and_proxy_url(val, host_root)
                                return f"{prefix}{val}{quote}"
                            line = re.sub(r'((?:URI|URL)=["\']?)([^"\']*)(["\']?)', fix_sub_url, line)
                        
                        new_lines.append(line)
                    else:
                        # Normal .ts veya .m4s segment satırı
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

        # .ts veya .m4s segmentlerini direkt stream et
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
