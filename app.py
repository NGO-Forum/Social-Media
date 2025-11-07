from flask import Flask, render_template, request, render_template_string, redirect, url_for, session
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import os, requests, json, tweepy
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload
from moviepy.editor import ImageClip, concatenate_videoclips, AudioFileClip
from datetime import datetime, timedelta, timezone
from PIL import Image
from apscheduler.schedulers.background import BackgroundScheduler
from flask_sqlalchemy import SQLAlchemy
from flask import send_from_directory
from google.auth.transport.requests import Request
from pathlib import Path

# Fix for Pillow >= 10 / Python 3.13
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

dotenv_path = Path(__file__).parent / ".env"
load_dotenv()  # Load tokens from .env

app = Flask(__name__)
app.secret_key = "@Riti#NGOF2025"


# MySQL connection
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:@localhost/media'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


now = datetime.now(timezone.utc)

# --- Scheduler ---
scheduler = BackgroundScheduler()
scheduler.start()


# Post model
class Post(db.Model):
    __tablename__ = 'posts'  # explicitly use your actual table name
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text, nullable=False)
    images = db.Column(db.Text, nullable=True)  # comma-separated paths
    scheduled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), nullable=True)
    posted = db.Column(db.Boolean, default=False)


# --- User credentials from environment variables ---
USERS = {}
for i in range(1, 10):  # supports up to 9 users, change if needed
    email = os.getenv(f"USER_{i}_EMAIL")
    password = os.getenv(f"USER_{i}_PASSWORD")
    if email and password:
        USERS[email] = generate_password_hash(password)


SOCIAL_API = {
    "twitter": {
        "api_key": os.getenv("TWITTER_API_KEY"),
        "api_secret": os.getenv("TWITTER_API_SECRET_KEY"),
        "access_token": os.getenv("TWITTER_ACCESS_TOKEN"),
        "access_secret": os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
    },
    "facebook": {
        "access_token": os.getenv("FB_PAGE_ACCESS_TOKEN"),
        "page_id": os.getenv("FB_PAGE_ID")
    },
    "instagram": {
        "access_token": os.getenv("INSTAGRAM_ACCESS_TOKEN"),
        "instagram_id": os.getenv("INSTAGRAM_BUSINESS_ID")
    },
    "youtube": {
        "creds_file": "token.json"  # Keep as is if using YouTube OAuth
    },
    "linkedin": {
        "client_id": os.getenv("LINKEDIN_CLIENT_ID"),
        "client_secret": os.getenv("LINKEDIN_CLIENT_SECRET"),
        "redirect_uri": os.getenv("LINKEDIN_REDIRECT_URI"),
        "tokens_file": "linkedin_tokens.json",  # local file for token storage
        "organization_id": os.getenv("LINKEDIN_PERSON_ID")  # or ORG_ID if posting as company
    },
    "tiktok": {
        "client_key": os.getenv("TIKTOK_CLIENT_KEY"),       # Your App ID
        "client_secret": os.getenv("TIKTOK_CLIENT_SECRET"), # Your App Secret
        "redirect_uri": os.getenv("TIKTOK_REDIRECT_URI"),   # OAuth redirect URI
        "business_id": os.getenv("TIKTOK_BUSINESS_ID"),     # TikTok business account ID
        "tokens_file": "tiktok_tokens.json" 
    }
}

SCOPES_YOUTUBE = ["https://www.googleapis.com/auth/youtube.upload"]


# --- Helper functions for login ---
def login_required(func):
    """Decorator to protect routes"""
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper

# --- Routes for authentication ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        if email in USERS and check_password_hash(USERS[email], password):
            session["user"] = email
            return redirect(url_for("index"))
        else:
            return render_template("login.html", error="Invalid email or password")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


# --- Slideshow creator ---
def create_slideshow(image_paths, output_path, duration_per_image=2, music_path=None):
    clips = [ImageClip(path).set_duration(duration_per_image).resize(height=720) for path in image_paths]
    video = concatenate_videoclips(clips, method="compose")
    if music_path and os.path.exists(music_path):
        audio = AudioFileClip(music_path).volumex(0.2)
        video = video.set_audio(audio)
    video.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac")
    return output_path

# --- Posting functions ---
def post_twitter(title, desc):
    api_key = SOCIAL_API['twitter']['api_key']
    api_secret = SOCIAL_API['twitter']['api_secret']
    access_token = SOCIAL_API['twitter']['access_token']
    access_secret = SOCIAL_API['twitter']['access_secret']

    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
    api = tweepy.API(auth)

    text = f"{title}\n\n{desc}" if title else desc
    if not text.strip():
        print("❌ Twitter: empty text")
        return False

    try:
        api.update_status(text)
        print("✅ Twitter posted")
        return True
    except Exception as e:
        print("❌ Twitter failed:", e)
        return False
    

#--- Facebook/Instagram token refresh ---
def refresh_facebook_instagram_token():
    token = SOCIAL_API['facebook']['access_token']
    app_id = os.getenv("FB_APP_ID")
    app_secret = os.getenv("FB_APP_SECRET")

    url = f"https://graph.facebook.com/v19.0/oauth/access_token"\
          f"?grant_type=fb_exchange_token"\
          f"&client_id={app_id}"\
          f"&client_secret={app_secret}"\
          f"&fb_exchange_token={token}"

    r = requests.get(url)
    if r.status_code == 200:
        new_token = r.json().get("access_token")
        SOCIAL_API['facebook']['access_token'] = new_token
        SOCIAL_API['instagram']['access_token'] = new_token
        print("✅ Facebook/Instagram token refreshed!")
    else:
        print("❌ Facebook token refresh failed:", r.text)

scheduler.add_job(refresh_facebook_instagram_token, 'interval', days=1)


#--- Facebook posting with multiple images or single video ---
def post_facebook(title, desc, media_paths=None):
    """
    Post to Facebook Page. Supports text, multiple images, or a single video.
    Uses a Page Access Token and Page endpoints only.
    """
    text = (title + "\n\n" if title else "") + (desc if desc else "")
    page_id = SOCIAL_API['facebook']['page_id']
    token = SOCIAL_API['facebook']['access_token']

    try:
        # Separate images and videos
        images = []
        video = None

        if media_paths:
            for path in media_paths:
                if not os.path.exists(path):
                    continue
                ext = os.path.splitext(path)[1].lower()
                if ext in ['.jpg', '.jpeg', '.png', '.gif']:
                    images.append(path)
                elif ext in ['.mp4', '.mov', '.avi', '.mkv'] and video is None:
                    video = path  # Only one video allowed
                else:
                    print("Skipping unsupported file:", path)

        # --- Handle multiple images ---
        attached_media = []
        for img in images:
            with open(img, "rb") as f:
                url = f"https://graph.facebook.com/v17.0/{page_id}/photos"
                data = {"published": "false", "access_token": token}
                files = {"source": f}
                resp = requests.post(url, data=data, files=files)
                if resp.status_code in [200, 201]:
                    media_fbid = resp.json().get("id")
                    attached_media.append({"media_fbid": media_fbid})
                else:
                    print("Failed to upload image:", resp.text)

        # --- Create post for images ---
        if attached_media:
            post_url = f"https://graph.facebook.com/v17.0/{page_id}/feed"
            post_data = {
                "message": text,
                "attached_media": attached_media,
                "access_token": token
            }
            resp = requests.post(post_url, json=post_data)
            print("Facebook image post response:", resp.status_code, resp.text)
            if resp.status_code not in [200, 201]:
                return False

        # --- Handle single video ---
        if video:
            with open(video, "rb") as f:
                url = f"https://graph.facebook.com/v17.0/{page_id}/videos"
                data = {"description": text, "access_token": token}
                files = {"source": f}
                resp = requests.post(url, data=data, files=files)
                print("Facebook video post response:", resp.status_code, resp.text)
                if resp.status_code not in [200, 201]:
                    return False

        # If no media, post text only
        if not images and not video:
            url = f"https://graph.facebook.com/v17.0/{page_id}/feed"
            data = {"message": text, "access_token": token}
            resp = requests.post(url, data=data)
            print("Facebook text post response:", resp.status_code, resp.text)
            if resp.status_code not in [200, 201]:
                return False

        return True

    except Exception as e:
        print("Facebook post exception:", e)
        return False


#--- Instagram posting (single image) ---
def post_instagram(title, desc, media_path=None):
    text = f"{title}\n\n{desc}" if title else desc
    url = f"https://graph.facebook.com/v17.0/{SOCIAL_API['instagram']['instagram_id']}/media"
    data = {"image_url": media_path, "caption": text, "access_token": SOCIAL_API['instagram']['access_token']}
    resp = requests.post(url, data=data)
    if resp.status_code != 200:
        print("Instagram upload failed:", resp.text)
        return False
    creation_id = resp.json().get("id")
    publish_url = f"https://graph.facebook.com/v17.0/{SOCIAL_API['instagram']['instagram_id']}/media_publish"
    publish_resp = requests.post(publish_url, data={"creation_id": creation_id, "access_token": SOCIAL_API['instagram']['access_token']})
    return publish_resp.status_code == 200


#--- YouTube token refresh ---
def refresh_youtube_token():
    """Refresh YouTube OAuth token using refresh_token inside token.json."""
    try:
        creds_file = SOCIAL_API['youtube']['creds_file']

        if not os.path.exists(creds_file):
            print("⚠️ YouTube: token.json not found, cannot refresh.")
            return

        creds = Credentials.from_authorized_user_file(creds_file, SCOPES_YOUTUBE)

        # Refresh access token if expired & refresh_token is available
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

            with open(creds_file, "w") as f:
                f.write(creds.to_json())

            print("✅ YouTube token refreshed successfully.")
        else:
            print("✅ YouTube token is still valid.")

    except Exception as e:
        print("❌ YouTube refresh error:", e)

scheduler.add_job(refresh_youtube_token, 'interval', minutes=30)

# --- YouTube posting (with auto refresh) ---
def post_youtube(title, desc, media_path):
    creds_file = SOCIAL_API['youtube']['creds_file']

    if not os.path.exists(creds_file):
        print("❌ token.json missing — run YouTube OAuth script first!")
        return False

    # Load token
    creds = Credentials.from_authorized_user_file(creds_file, SCOPES_YOUTUBE)

    # ✅ Refresh token if needed
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(creds_file, "w") as f:
                f.write(creds.to_json())
            print("✅ YouTube token refreshed before upload.")
        except Exception as e:
            print("❌ Failed to refresh YouTube token:", e)
            return False

    try:
        youtube = build("youtube", "v3", credentials=creds)
        media = MediaFileUpload(media_path, chunksize=-1, resumable=True)

        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title if title else (desc[:50] if desc else "Video"),
                    "description": desc or ""
                },
                "status": {"privacyStatus": "public"}
            },
            media_body=media
        )

        response = request.execute()

        if "id" in response:
            print("✅ YouTube upload successful:", response["id"])
            return True

        print("❌ YouTube upload failed:", response)
        return False

    except Exception as e:
        print("❌ YouTube post exception:", e)
        return False



#--- TikTok posting ---
def post_tiktok(title, desc, media_path):
    text = f"{title}\n\n{desc}" if title else desc
    headers = {"Access-Token": SOCIAL_API['tiktok']['access_token']}
    upload_url = "https://business-api.tiktokglobalshop.com/open_api/v1.3/media/upload/"
    files = {"video_file": open(media_path, "rb")}
    resp = requests.post(upload_url, files=files, headers=headers)
    if resp.status_code != 200: return False
    media_id = resp.json().get("data", {}).get("video_id")
    post_url = "https://business-api.tiktokglobalshop.com/open_api/v1.3/post/create/"
    post_resp = requests.post(post_url, json={"business_id": SOCIAL_API['tiktok']['business_id'], "video_id": media_id, "caption": text}, headers=headers)
    return post_resp.status_code == 200


# --- Token helpers ---
def save_tiktok_tokens(tokens):
    with open(SOCIAL_API["tiktok"]["tokens_file"], "w") as f:
        json.dump(tokens, f, indent=4)

def load_tiktok_tokens():
    path = SOCIAL_API["tiktok"]["tokens_file"]
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return None

def get_tiktok_access_token():
    tokens = load_tiktok_tokens()
    if not tokens:
        print("❌ No TikTok token found. Please login via /tiktok/login")
        return None

    expires_at = datetime.fromisoformat(tokens["expires_at"])
    if datetime.now(timezone.utc) >= expires_at:
        # Refresh token
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            print("❌ No refresh token. Reauthorize via /tiktok/login")
            return None

        data = {
            "client_key": SOCIAL_API["tiktok"]["client_key"],
            "client_secret": SOCIAL_API["tiktok"]["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": refresh_token
        }
        r = requests.post("https://business-api.tiktokglobalshop.com/open_api/v1.3/oauth/refresh_token/", json=data)
        if r.status_code != 200:
            print("❌ Failed to refresh TikTok token:", r.text)
            return None

        resp_data = r.json().get("data", {})
        tokens["access_token"] = resp_data["access_token"]
        tokens["refresh_token"] = resp_data["refresh_token"]
        tokens["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=int(resp_data["expires_in"]))).isoformat()
        save_tiktok_tokens(tokens)
        print("✅ TikTok token refreshed automatically")

    return tokens["access_token"]


# --- OAuth login ---
@app.route("/tiktok/login")
def tiktok_login():
    client_key = SOCIAL_API["tiktok"]["client_key"]
    redirect_uri = SOCIAL_API["tiktok"]["redirect_uri"]
    scopes = "video.create video.list user.info.basic"
    auth_url = (
        f"https://business-api.tiktokglobalshop.com/open_api/v1.3/oauth/authorize/"
        f"?client_key={client_key}&response_type=code&scope={scopes}&redirect_uri={redirect_uri}"
    )
    return redirect(auth_url)

@app.route("/tiktok/callback")
def tiktok_callback():
    code = request.args.get("code")
    if not code:
        return "No code received", 400

    data = {
        "client_key": SOCIAL_API["tiktok"]["client_key"],
        "client_secret": SOCIAL_API["tiktok"]["client_secret"],
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": SOCIAL_API["tiktok"]["redirect_uri"]
    }
    r = requests.post("https://business-api.tiktokglobalshop.com/open_api/v1.3/oauth/token/", json=data)
    if r.status_code != 200:
        return f"Token exchange failed: {r.text}", 400

    resp_data = r.json().get("data", {})
    tokens = {
        "access_token": resp_data["access_token"],
        "refresh_token": resp_data.get("refresh_token"),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=int(resp_data["expires_in"]))).isoformat()
    }
    save_tiktok_tokens(tokens)
    return "TikTok authorized successfully!"



# --- LinkedIn token handling ---
def save_linkedin_tokens(tokens):
    with open(SOCIAL_API['linkedin']['tokens_file'], "w") as f:
        json.dump(tokens, f, indent=4)

def load_linkedin_tokens():
    path = SOCIAL_API['linkedin']['tokens_file']
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return None

def get_linkedin_access_token():
    tokens = load_linkedin_tokens()
    if not tokens:
        print("❌ No LinkedIn tokens found. Please login via /linkedin/login")
        return None

    expires_at = datetime.fromisoformat(tokens["expires_at"])
    if datetime.now(timezone.utc) >= expires_at:
        # Access token expired, try refresh
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            print("❌ No refresh token, reauthorize via /linkedin/login")
            return None

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": SOCIAL_API['linkedin']['client_id'],
            "client_secret": SOCIAL_API['linkedin']['client_secret']
        }
        r = requests.post("https://www.linkedin.com/oauth/v2/accessToken", data=data)
        if r.status_code != 200:
            print("❌ Failed to refresh LinkedIn token:", r.text)
            return None

        resp_data = r.json()
        tokens["access_token"] = resp_data["access_token"]
        tokens["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=resp_data["expires_in"])).isoformat()
        if "refresh_token" in resp_data:
            tokens["refresh_token"] = resp_data["refresh_token"]
        save_linkedin_tokens(tokens)
        print("✅ LinkedIn token refreshed successfully")

    return tokens["access_token"]

def refresh_linkedin_token():
    tokens = load_linkedin_tokens()
    if not tokens:
        print("❌ No LinkedIn tokens to refresh")
        return

    expires_at = datetime.fromisoformat(tokens["expires_at"])
    # Refresh 1 hour before expiry
    if datetime.now(timezone.utc) + timedelta(hours=1) >= expires_at:
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            print("❌ No refresh token, user needs to login again via /linkedin/login")
            return

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": SOCIAL_API['linkedin']['client_id'],
            "client_secret": SOCIAL_API['linkedin']['client_secret']
        }
        r = requests.post("https://www.linkedin.com/oauth/v2/accessToken", data=data)
        if r.status_code == 200:
            resp_data = r.json()
            tokens["access_token"] = resp_data["access_token"]
            tokens["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=resp_data["expires_in"])).isoformat()
            if "refresh_token" in resp_data:
                tokens["refresh_token"] = resp_data["refresh_token"]
            save_linkedin_tokens(tokens)
            print("✅ LinkedIn token refreshed automatically")
        else:
            print("❌ Failed to refresh LinkedIn token:", r.text)

# Schedule LinkedIn token refresh every 30 minutes
scheduler.add_job(refresh_linkedin_token, 'interval', minutes=30)

# --- Post LinkedIn org with title, description, 3 images ---
def post_linkedin_org(title=None, description=None, image_paths=None):
    access_token = get_linkedin_access_token()
    if not access_token:
        print("❌ No access token — visit /linkedin/login first")
        return False

    org_urn = f"urn:li:organization:{SOCIAL_API['linkedin']['organization_id']}"
    text = ""
    if title and description:
        text = f"{title}\n\n{description}"
    elif title:
        text = title
    elif description:
        text = description

    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json"
    }

    # Upload images (up to 3)
    assets = []
    if image_paths:
        for path in image_paths[:3]:
            reg_resp = requests.post(
                "https://api.linkedin.com/v2/assets?action=registerUpload",
                headers=headers,
                json={
                    "registerUploadRequest": {
                        "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                        "owner": org_urn,
                        "serviceRelationships": [{"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}]
                    }
                }
            )
            if reg_resp.status_code not in [200,201]:
                print("❌ Register upload failed:", reg_resp.text)
                return False

            reg_data = reg_resp.json()
            upload_url = reg_data['value']['uploadMechanism']['com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest']['uploadUrl']
            asset = reg_data['value']['asset']

            with open(path, "rb") as f:
                upload_resp = requests.put(upload_url, data=f, headers={"Authorization": f"Bearer {access_token}", "Content-Type": "image/jpeg"})
            if upload_resp.status_code not in [200,201]:
                print("❌ Upload failed:", upload_resp.text)
                return False

            assets.append({
                "status": "READY",
                "description": {"text": "Uploaded via API"},
                "media": asset,
                "title": {"text": os.path.basename(path)}
            })

    # Create the post
    post_data = {
        "author": org_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "IMAGE" if assets else "NONE",
                "media": assets
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
    }

    r = requests.post("https://api.linkedin.com/v2/ugcPosts", headers=headers, json=post_data)
    if r.status_code in [200,201]:
        print("✅ LinkedIn post created successfully!")
        return True
    else:
        print("❌ Failed:", r.status_code, r.text)
        return False

# LinkedIn OAuth login
@app.route("/linkedin/login")
def linkedin_login():
    client_id = SOCIAL_API['linkedin']['client_id']
    redirect_uri = SOCIAL_API['linkedin']['redirect_uri']
    scopes = "w_organization_social r_organization_social"
    auth_url = (
        "https://www.linkedin.com/oauth/v2/authorization"
        f"?response_type=code&client_id={client_id}"
        f"&redirect_uri={requests.utils.requote_uri(redirect_uri)}"
        f"&scope={requests.utils.requote_uri(scopes)}"
    )
    return redirect(auth_url)

@app.route("/linkedin/callback")
def linkedin_callback():
    code = request.args.get("code")
    if not code:
        return "No code received", 400

    resp = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": SOCIAL_API['linkedin']['redirect_uri'],
            "client_id": SOCIAL_API['linkedin']['client_id'],
            "client_secret": SOCIAL_API['linkedin']['client_secret']
        }
    )
    if resp.status_code != 200:
        return f"Token exchange failed: {resp.text}", 400
    data = resp.json()
    tokens = {
        "access_token": data["access_token"],
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"]))).isoformat()
    }
    if "refresh_token" in data:
        tokens["refresh_token"] = data["refresh_token"]
    save_linkedin_tokens(tokens)
    return "LinkedIn authorized successfully!"


# --- Routes ---
@app.route("/", methods=["GET"])
@login_required
def index():
    return render_template("index.html")

@app.route("/", methods=["POST"])
@login_required
def post_all():
    selected_platforms = request.form.getlist("platforms")
    title = request.form.get("title")
    desc = request.form.get("desc")  # English
    title_kh = request.form.get("title_kh")  # Khmer title
    desc_kh = request.form.get("desc_kh")    # Khmer description
    scheduled_time_str = request.form.get("scheduled_at")

    media_files = request.files.getlist("media[]")
    media_paths = []
    for file in media_files:
        filename = secure_filename(file.filename)
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(path)
        media_paths.append(path)

    # --- Save post to database ---
    images_str = ",".join(media_paths) if media_paths else None
    scheduled_at = datetime.strptime(scheduled_time_str, "%Y-%m-%dT%H:%M") if scheduled_time_str else None

    new_post = Post(
        title=title,
        description=desc,
        images=images_str,
        scheduled_at=scheduled_at
    )

    db.session.add(new_post)
    db.session.commit()


    
    # --- Function to post to all selected platforms ---
    def do_post(post_obj):
        Done, Failed = [], []

        # Create slideshow for YouTube/TikTok if multiple images
        slideshow_path = None
        if len(media_paths) > 1 and any(p in selected_platforms for p in ["youtube", "tiktok"]):
            slideshow_path = os.path.join(app.config['UPLOAD_FOLDER'], f"slideshow_{datetime.now().strftime('%Y%m%d%H%M%S')}.mp4")
            try:
                slideshow_path = create_slideshow(media_paths, slideshow_path)
            except Exception as e:
                print("❌ Failed to create slideshow:", e)
                slideshow_path = None

        # --- Facebook: use English + Khmer together ---
        if "facebook" in selected_platforms:
            fb_title = ""  # Facebook mainly uses description
            fb_desc_parts = []

            # Add Khmer first
            if title_kh:
                fb_desc_parts.append(title_kh)
            if desc_kh:
                fb_desc_parts.append(desc_kh)

            # Add English after Khmer
            if title:
                fb_desc_parts.append(title)
            if desc:
                fb_desc_parts.append(desc)

            fb_desc = "\n\n".join(fb_desc_parts)  # Join all parts with line breaks

            success = post_facebook(fb_title, fb_desc, media_paths if media_paths else None)
            if success:
                Done.append("Facebook")
            else:
                Failed.append("Facebook")


        for platform in selected_platforms:
            if platform == "facebook":
                continue
            try:
                success = False
                if platform == "twitter":
                    success = post_twitter(title, desc)
                elif platform == "instagram":
                    success = media_paths and post_instagram(title, desc, media_paths[:3])
                elif platform == "youtube":
                    success = (slideshow_path or media_paths) and post_youtube(title, desc, slideshow_path or media_paths[0])
                elif platform == "linkedin":
                    success = media_paths and post_linkedin_org(title, desc, media_paths[:3])
                elif platform == "tiktok":
                    success = (slideshow_path or media_paths) and post_tiktok(title, desc, slideshow_path or media_paths[0])

                if success:
                    Done.append(platform.capitalize())
                else:
                    Failed.append(platform.capitalize())

            except Exception as e:
                print(f"❌ {platform} post failed:", e)
                Failed.append(platform.capitalize())
        
        # --- Mark post as posted ---
        post_obj.posted = True
        db.session.commit()
        return Done, Failed

    # --- Check if scheduled ---
    if scheduled_time_str:
        scheduled_time = datetime.strptime(scheduled_time_str, "%Y-%m-%dT%H:%M")
        scheduler.add_job(lambda: do_post(new_post), 'date', run_date=scheduled_time)
        return render_template_string("""
        <html>
        <head><script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script></head>
        <body>
            <script>
            {% raw %}
                Swal.fire({
                    icon: 'success',
                    title: 'Scheduled!',
                    html: '✅ Your post is scheduled for: {% endraw %}{{ scheduled_time }}{% raw %}',
                    confirmButtonColor: '#22c55e'
                }).then(()=>{window.location.href='/'});
            {% endraw %}
            </script>
        </body>
        </html>
        """, scheduled_time=scheduled_time.strftime("%Y-%m-%d %H:%M"))

    
    # Post immediately
    Done, Failed = do_post(new_post)

    # --- Return results (fixed Jinja + JS) ---
    if Done:
        platforms_html = "<br>".join(Done)
        return render_template_string("""
        <!DOCTYPE html>
        <html lang="en">
        <head><meta charset="UTF-8"><title>Posting Result</title>
        <script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script></head>
        <body>
        <script>
        {% raw %}
            Swal.fire({
                icon: 'success',
                title: 'Posted Successfully!',
                html: '{% endraw %}{{ platforms_html|safe }}{% raw %}',
                confirmButtonColor: '#28a745'
            }).then(() => { window.location.href = '/'; });
        {% endraw %}
        </script>
        </body>
        </html>
        """, platforms_html=platforms_html)

    elif Failed:
        platforms_html = "<br>".join(Failed)
        return render_template_string("""
        <html><head><script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script></head>
        <body>
        <script>
        {% raw %}
            Swal.fire({
                icon: 'error',
                title: 'Failed!',
                html: '❌ Failed to post: {% endraw %}{{ platforms_html|safe }}{% raw %}',
                confirmButtonColor: '#dc3545'
            }).then(() => { window.location.href = '/'; });
        {% endraw %}
        </script>
        </body>
        </html>
        """, platforms_html=platforms_html)

    else:
        return render_template_string("""
        <html><head><script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script></head>
        <body>
        <script>
        {% raw %}
            Swal.fire({
                icon: 'warning',
                title: 'No platforms selected!',
                text: 'Please choose at least one platform to post to.',
                confirmButtonColor: '#f39c12'
            }).then(() => { window.location.href = '/'; });
        {% endraw %}
        </script>
        </body>
        </html>
        """)

    
# ---------- Simple health route ----------
@app.route("/status")
def status():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

@app.template_filter('basename')
def basename_filter(path):
    import os
    return os.path.basename(path)


@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route("/posts")
@login_required
def show_posts():
    posts = Post.query.order_by(Post.created_at.desc()).all()  # latest first
    return render_template("posts.html", posts=posts)


if __name__ == "__main__":
    # Ensure LinkedIn env variables exist (warn but still run)
    if not SOCIAL_API['linkedin']['client_id'] or not SOCIAL_API['linkedin']['client_secret'] or not SOCIAL_API['linkedin']['organization_id']:
        print("WARNING: LinkedIn client_id, client_secret or organization_id not set in environment. Visit /linkedin/login will fail until set.")
    app.run(host="0.0.0.0", port=5000, debug=True)
