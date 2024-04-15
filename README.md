# finai
Chat with various document types - XLSX, PPTX, DOCX, PDF, CSV, TXT - using any LLM models using ollama.
For Financial documents like Earning call, 10-k automatic prompts to get more insightful information.

## Getting started

Clone the repo

## Bring up the server
docker-compose up 

## Visit Ollama WebUI 
http://localhost:3002

## Download Your Models
Using Ollama WebUI download your model(s)

## Start Packet Raptor
http://localhost:8510

### Usage
This has been tested with a variety of small to medium size files with success. For larger files use Document RAPTOR should work fine

Upload your Document
Pick Your Model
Wait and review the Tree 
Ask any outstanding questions 

### Download company Data
from sec_edgar_downloader import Downloader
dl = Downloader("MyCompanyName", "my.email@domain.com")
dl.get("8-K", "AAPL")

### Earning Call download from youtube
from youtube_transcript_api import YouTubeTranscriptApi
# Replace 'VIDEO_ID' with the ID of the video you want to transcribe
transcript = YouTubeTranscriptApi.get_transcript('VIDEO_ID')
print(transcript)


## Thank you
