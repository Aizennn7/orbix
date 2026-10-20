package com.aizennn7.orbix;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Two sources, one screen.
 *
 * If the Termux-hosted server answers on 127.0.0.1:8000 the app loads it and you
 * get the live application. Otherwise it serves the bundled snapshot from the
 * APK's assets over a virtual https origin, so the same page code, relative
 * paths and fetch() calls all work with no network at all.
 */
public class MainActivity extends Activity {

    private static final String LIVE_URL = "http://127.0.0.1:8000/";
    private static final String SNAPSHOT_URL = "https://orbix.local/index.html";
    private static final String ASSET_HOST = "orbix.local";
    private static final int REQ_FILE_CHOOSER = 4001;

    private WebView web;
    private TextView status;
    private ProgressBar progress;
    private ValueCallback<Uri[]> pendingFiles;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        setContentView(R.layout.activity_main);

        web = findViewById(R.id.web);
        status = findViewById(R.id.status);
        progress = findViewById(R.id.progress);

        findViewById(R.id.reload).setOnClickListener(v -> resolveSource());
        configureWebView();

        if (state == null) {
            resolveSource();
        }
    }

    private void configureWebView() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setMediaPlaybackRequiresUserGesture(true);

        web.setBackgroundColor(0xFF0B0E14);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                if (url != null && ASSET_HOST.equals(url.getHost())) {
                    return serveAsset(url.getPath());
                }
                return null;
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                String host = url.getHost() == null ? "" : url.getHost();
                if (ASSET_HOST.equals(host) || "127.0.0.1".equals(host) || "localhost".equals(host)) {
                    return false;
                }
                // Broker links, docs, anything external: hand it to the browser.
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, url));
                } catch (ActivityNotFoundException ignored) {
                    // Nothing can open it; stay put rather than crash.
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                if (newProgress < 100) {
                    progress.setVisibility(View.VISIBLE);
                }
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (pendingFiles != null) {
                    pendingFiles.onReceiveValue(null);
                }
                pendingFiles = callback;
                try {
                    startActivityForResult(params.createIntent(), REQ_FILE_CHOOSER);
                    return true;
                } catch (ActivityNotFoundException e) {
                    pendingFiles = null;
                    return false;
                }
            }
        });
    }

    /** Prefer the live server; fall back to the bundled snapshot. */
    private void resolveSource() {
        status.setText(R.string.status_checking);
        progress.setVisibility(View.VISIBLE);

        new Thread(() -> {
            final boolean up = localServerUp();
            runOnUiThread(() -> {
                if (up) {
                    status.setText(R.string.status_live);
                    web.loadUrl(LIVE_URL);
                } else {
                    status.setText(R.string.status_offline);
                    web.loadUrl(SNAPSHOT_URL);
                }
            });
        }, "orbix-source").start();
    }

    private boolean localServerUp() {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(LIVE_URL + "healthz").openConnection();
            connection.setConnectTimeout(800);
            connection.setReadTimeout(800);
            connection.setUseCaches(false);
            connection.setRequestProperty("Connection", "close");
            return connection.getResponseCode() == 200;
        } catch (Exception e) {
            return false;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    /** Serve a bundled file, so the snapshot works offline and over fetch(). */
    private WebResourceResponse serveAsset(String rawPath) {
        String path = rawPath == null ? "" : rawPath;
        int query = path.indexOf('?');
        if (query >= 0) {
            path = path.substring(0, query);
        }
        while (path.startsWith("/")) {
            path = path.substring(1);
        }
        if (path.isEmpty() || path.endsWith("/")) {
            path = path + "index.html";
        }

        String mime = mimeFor(path);
        try {
            InputStream stream = getAssets().open(path);
            String encoding = needsUtf8(mime) ? "UTF-8" : null;
            WebResourceResponse response = new WebResourceResponse(mime, encoding, stream);
            response.setStatusCodeAndReasonPhrase(200, "OK");
            return response;
        } catch (IOException missing) {
            return null;
        }
    }

    private static boolean needsUtf8(String mime) {
        return mime.startsWith("text/")
                || mime.equals("application/javascript")
                || mime.equals("application/json")
                || mime.equals("image/svg+xml");
    }

    private static String mimeFor(String path) {
        if (path.endsWith(".html") || path.endsWith(".htm")) return "text/html";
        if (path.endsWith(".js")) return "application/javascript";
        if (path.endsWith(".css")) return "text/css";
        if (path.endsWith(".json")) return "application/json";
        if (path.endsWith(".svg")) return "image/svg+xml";
        if (path.endsWith(".png")) return "image/png";
        if (path.endsWith(".ico")) return "image/x-icon";
        if (path.endsWith(".woff2")) return "font/woff2";
        if (path.endsWith(".woff")) return "font/woff";
        return "application/octet-stream";
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ_FILE_CHOOSER) {
            if (pendingFiles != null) {
                pendingFiles.onReceiveValue(
                        WebChromeClient.FileChooserParams.parseResult(resultCode, data));
                pendingFiles = null;
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            web.destroy();
        }
        super.onDestroy();
    }
}
