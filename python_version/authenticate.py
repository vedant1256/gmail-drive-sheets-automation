from config import CREDENTIALS_FILE, SCOPES, TOKEN_FILE, USER_EMAIL
from pathlib import Path

def main():
    email_label = USER_EMAIL or "Google Account"
    print("=" * 65)
    print(f"🔐 One-Time Google Authentication for {email_label}")
    print("=" * 65)
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(
        port=0,
        prompt="consent",
        access_type="offline",
        login_hint=USER_EMAIL or None
    )
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    with open(Path(TOKEN_FILE).parent.parent / "token.json", "w") as f:
        f.write(creds.to_json())
    print(f"\n✓ SUCCESS: {email_label} is now authenticated!")
    print(f"✓ Saved refresh token to: {TOKEN_FILE}")
    print("You can now refresh http://localhost:3000 to see connected status!")

if __name__ == "__main__":
    main()
