from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
import json

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

flow = InstalledAppFlow.from_client_secrets_file(
    "API/client_secret.json",
    SCOPES
)

creds = flow.run_local_server(port=0)  # Opens browser for OAuth

# creds = flow.run_console()  # Shows URL + paste code manually

# Save token
with open("token.json", "w") as token_file:
    token_file.write(creds.to_json())

print("token.json created successfully!")
