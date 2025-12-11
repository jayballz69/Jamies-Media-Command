# 🎬 Jamie's Media Command

**The ultimate command center for Plex curation and automation.**

Jamie's Media Command is a modern, Python-based dashboard that bridges the gap between **Plex**, **Radarr**, **Sonarr**, and **Trakt**. It allows you to build massive collections in seconds, automatically find missing media, and monitor the download status of hundreds of items in real-time.

![Python](https://img.shields.io/badge/Python-3.x-blue.svg)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-green.svg)
![License](https://img.shields.io/badge/License-MIT-orange.svg)

## ✨ Features

* **Modern GUI:** Built with `CustomTkinter` for a clean, dark-mode interface.
* **Collection Automation:** Paste a list of 100 movies, and the app will:
    * Tag the ones you already have in Plex.
    * Send the ones you are missing to **Radarr** or **Sonarr**.
* **Trakt Integration:** Search for public lists (e.g., "Best 80s Horror", "IMDb Top 250") and import them directly into your workflow.
* **Active Monitoring:** A dashboard that tracks "Missing" items and automatically marks them as "Complete" once they finish downloading and appear in Plex.
* **Smart Matching:** Uses fuzzy logic matching to handle slight naming differences (e.g., "Star Wars: A New Hope" vs "Star Wars Episode IV").
* **Smart Merging:** Add new items to existing collections without re-scanning the entire library.

## 🛠️ Prerequisites

You need **Python 3.10+** installed on your system.

## 📦 Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/jayballz69/Jamies-Media-Command.git
    cd Jamies-Media-Command
    python jamies_media_command.py

2.  **Install dependencies:**
    ```bash
    pip install customtkinter plexapi requests
    ```

3.  **Run the application:**
    ```bash
    python plex_manager_pro.py
    ```

## ⚙️ Configuration

On the first launch, navigate to the **Settings** tab. You will need to provide:

* **Plex:** URL (e.g., `http://127.0.0.1:32400`) and your X-Plex-Token.
* **Radarr/Sonarr:** URL, API Key, Root Folder path, and Quality Profile ID.
* **Trakt:** Client ID (get this from [Trakt API](https://trakt.tv/oauth/applications)).

*> **Note:** Your keys are saved locally in `collection_manager_config.json`. This file is ignored by Git to keep your secrets safe.*

## 🚀 How to Use

### 1. Create a Collection
* Go to **"Create Collection"**.
* Select **Movie** (Radarr) or **TV Show** (Sonarr).
* Name your collection (e.g., "True Crime 2025").
* Paste a list of titles (Format: `Title (Year)`).
* Click **Process**.

### 2. Monitor Progress
* Switch to **"Active Monitor"**.
* You will see a progress bar for your collection.
* **Green:** In Plex.
* **Yellow:** Sent to Downloader (Pending).
* Enable **Auto-scan** to let the app check for new arrivals every 10 minutes.

### 3. Import from Trakt
* Go to **"Trakt Import"**.
* Search for a list (e.g., "Marvel").
* Preview the contents on the right.
* Click **Import** to send it to the creation tab.

## ⚠️ Disclaimer

This tool interacts
