from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import os
import asyncio
import time
import aiohttp
import html2text
from selectolax.parser import HTMLParser
from readabilipy import simple_json_from_html_string
import concurrent.futures
from urllib.parse import urljoin, urlparse
import uuid

# Suppress Node.js deprecation warnings
os.environ['NODE_NO_WARNINGS'] = '1'
jobs = {}
app = FastAPI()

API_KEY = "AIzaSyBoCNgf7zyxkWdhJ43-PSP5dRPbrV9U72c"
SEARCH_ENGINE_ID = "93eb093bb82b44a24"
NUM_RESULTS = 10

templates = Jinja2Templates(directory="templates")

# Cache to store HTML content
html_cache = {}

async def download_link(url: str, session: aiohttp.ClientSession, use_readability: bool):
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        try:
            if url in html_cache:
                html_content = html_cache[url]
                print(f"Using cached content for URL: {url}")
            else:
                async with session.get(url) as response:
                    html_content = await response.text()
                    html_cache[url] = html_content
                    print(f"Fetched and cached content for URL: {url}")

            tree = HTMLParser(html_content)
            try:
                title_element = tree.css_first('title')
                title = title_element.text() if title_element else 'No Title'
            except Exception as e:
                print(f"Error parsing CSS: {e}")
                title = 'No Title'

            text_content = ""
            if use_readability:
                try:
                    article = await loop.run_in_executor(pool, simple_json_from_html_string, html_content, True)
                    text_content = await loop.run_in_executor(pool, html2text.HTML2Text().handle, article['content']) if article.get('plain_content') else 'No Content'
                    print("Readability content processed successfully.")
                except Exception as e:
                    print(f"Error processing readability: {e}")
                    text_content = 'No Content'
            else:
                text_content = await loop.run_in_executor(pool, html2text.HTML2Text().handle, html_content)
                print("HTML content converted to text successfully.")

            formatted_content = f"Title: {title}\n\nURL Source: {url}\n\nMarkdown Content: {title}\n===============\n\n{text_content}"
            return formatted_content
        except Exception as e:
            print(f"Error fetching or processing the URL: {e}")
            return None

async def download_all(urls, session, use_readability):
    tasks = [download_link(url, session, use_readability) for url in urls]
    return await asyncio.gather(*tasks)

@app.post("/download")
async def download(url: str = Form(...), use_readability: str = Form('false')):
    use_readability = use_readability.lower() == 'true'
    url_list = [url]
    connector = aiohttp.TCPConnector(limit=200)
    async with aiohttp.ClientSession(connector=connector) as session:
        start = time.time()
        results = await download_all(url_list, session, use_readability)
        end = time.time()
    time_taken = end - start
    text_content = results[0] if results else ""
    return JSONResponse(content={'time_taken': time_taken, 'content': text_content})

async def download_and_extract_links(url: str, session: aiohttp.ClientSession, use_readability: bool):
    try:
        if url in html_cache:
            html_content = html_cache[url]
            print(f"Using cached content for URL: {url}")
        else:
            async with session.get(url) as response:
                html_content = await response.text()
                html_cache[url] = html_content
                print(f"Fetched and cached content for URL: {url}")

        tree = HTMLParser(html_content)
        links = [urljoin(url, link.attrs['href']) for link in tree.css('a[href]')
            if urlparse(urljoin(url, link.attrs['href'])).netloc == urlparse(url).netloc]

        title_element = tree.css_first('title')
        title = title_element.text() if title_element else 'No Title'

        text_content = ""
        if use_readability:
            article = simple_json_from_html_string(html_content, True)
            text_content = html2text.HTML2Text().handle(article['content']) if article.get('plain_content') else 'No Content'
        else:
            text_content = html2text.HTML2Text().handle(html_content)

        formatted_content = f"Title: {title}\n\nURL Source: {url}\n\nMarkdown Content: {title}\n===============\n\n{text_content}"
        return formatted_content, links
    except Exception as e:
        print(f"Error fetching or processing the URL {url}: {e}")
        return None, []

async def crawl_job(job_id: str, start_url: str, use_readability: bool, limit: int):
    connector = aiohttp.TCPConnector(limit=200)
    async with aiohttp.ClientSession(connector=connector) as session:
        urls_to_crawl = [start_url]
        crawled_urls = set()
        results = []
        start_time = time.time()

        while urls_to_crawl and len(crawled_urls) < limit:
            url = urls_to_crawl.pop(0)
            if url not in crawled_urls:
                content, new_links = await download_and_extract_links(url, session, use_readability)
                if content:
                    results.append(content)
                    crawled_urls.add(url)
                    urls_to_crawl.extend([link for link in new_links if link not in crawled_urls and link not in urls_to_crawl])

                jobs[job_id]['progress'] = len(crawled_urls)

        end_time = time.time()
        time_taken = end_time - start_time

    jobs[job_id]['status'] = 'completed'
    jobs[job_id]['results'] = results
    jobs[job_id]['time_taken'] = time_taken

@app.post("/search")
async def search(query: str = Form(...), use_readability: str = Form('false')):
    use_readability = use_readability.lower() == 'true'
    search_url = f'https://www.googleapis.com/customsearch/v1?key={API_KEY}&cx={SEARCH_ENGINE_ID}&q={query}&num={NUM_RESULTS}'
    start = time.time()
    results = await search_and_download(search_url, use_readability)
    end = time.time()
    time_taken = end - start
    return JSONResponse(content={'time_taken': time_taken, 'results': results})

async def search_and_download(search_url, use_readability):
    async with aiohttp.ClientSession() as session:
        async with session.get(search_url) as response:
            search_results = await response.json()
            print(f"Search results: {search_results}")  # Log the search results
            urls = [item['link'] for item in search_results.get('items', [])]
            if not urls:
                print("No URLs found in search results.")
            downloaded_contents = await download_all(urls, session, use_readability)
            return downloaded_contents

@app.post("/crawl")
async def crawl(url: str = Form(...), use_readability: str = Form('false'), limit: int = Form(10)):
    use_readability = use_readability.lower() == 'true'
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        'status': 'in_progress',
        'progress': 0,
        'limit': limit,
        'results': []
    }

    asyncio.create_task(crawl_job(job_id, url, use_readability, limit))

    return JSONResponse(content={'job_id': job_id, 'message': 'Crawl job started'})

@app.get("/job/{job_id}")
async def get_job_status(job_id: str):
    if job_id not in jobs:
        return JSONResponse(content={'error': 'Job not found'}, status_code=404)

    job = jobs[job_id]
    return JSONResponse(content={
        'status': job['status'],
        'progress': job['progress'],
        'limit': job['limit'],
        'time_taken': job.get('time_taken', None),
        'results': job['results'] if job['status'] == 'completed' else []
    })

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return HTMLResponse(content=templates.TemplateResponse("index.html", {"request": request}).body)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)