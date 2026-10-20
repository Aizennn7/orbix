package com.orbix.signallab;

import android.os.Bundle;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebSettings;
import android.webkit.WebChromeClient;
import android.content.Intent;
import android.net.Uri;
import androidx.appcompat.app.AppCompatActivity;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import java.util.concurrent.TimeUnit;

// Chaquopy Python
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

/**
 * Orbix MainActivity - All Orbix inside APK
 * - WebView loads local PWA with offline SQLite DB
 * - Chaquopy runs Python FastAPI backend inside APK (mobile_api.py)
 * - WorkManager for 24/7 background signal collection
 */
public class MainActivity extends AppCompatActivity {
    private WebView webView;
    private Python python;
    private static final String TAG = "OrbixAPK";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        
        // Init Chaquopy Python - All Orbix Python inside APK
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(this));
        }
        python = Python.getInstance();
        
        // Start Python backend in background thread (FastAPI mobile)
        new Thread(() -> {
            try {
                // This runs orbix/mobile_api.py inside APK
                python.getModule("orbix.mobile_api");
                android.util.Log.i(TAG, "Python Orbix backend started inside APK");
            } catch (Exception e) {
                android.util.Log.e(TAG, "Python backend failed: " + e.getMessage());
            }
        }).start();

        setContentView(R.layout.activity_main);
        
        webView = findViewById(R.id.webview);
        setupWebView();
        
        // Load local assets - offline first, all inside APK
        // For Capacitor build, this will be dist/index.html
        // For fallback, load from assets
        webView.loadUrl("file:///android_asset/index.html");
        
        // Schedule 24/7 background work - signal collection even when app in background
        scheduleBackgroundWork();
    }

    private void setupWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setAllowFileAccessFromFileURLs(true);
        settings.setAllowUniversalAccessFromFileURLs(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        
        // Enable offline SQLite via WebView
        settings.setDatabasePath(getDir("databases", 0).getPath());
        
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                if (url.startsWith("file://") || url.contains("orbix") || url.startsWith("https://appassets")) {
                    return false;
                }
                // Open external links in browser
                Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                startActivity(intent);
                return true;
            }
            
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                // Inject APK flag
                view.evaluateJavascript("window.ORBIX_APK=true; window.ORBIX_OFFLINE=true; console.log('Orbix APK - all inside');", null);
            }
        });
        
        webView.setWebChromeClient(new WebChromeClient());
        
        // Add JavaScript interface for native <-> Python bridge
        webView.addJavascriptInterface(new OrbixBridge(), "OrbixNative");
    }

    private void scheduleBackgroundWork() {
        // Every 15 min (Android minimum for periodic work) - collects M1 candles
        PeriodicWorkRequest work = new PeriodicWorkRequest.Builder(
            OrbixWorker.class, 15, TimeUnit.MINUTES
        ).build();
        WorkManager.getInstance(this).enqueue(work);
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    // Bridge for JS to call native/Python
    public class OrbixBridge {
        @android.webkit.JavascriptInterface
        public String getDatabasePath() {
            return getFilesDir().getAbsolutePath() + "/orbix_data";
        }
        
        @android.webkit.JavascriptInterface
        public String getPythonVersion() {
            try {
                return python.getModule("platform").callAttr("python_version").toString();
            } catch (Exception e) {
                return "Python in APK - Orbix";
            }
        }
        
        @android.webkit.JavascriptInterface
        public void vibrate(int duration) {
            android.os.Vibrator v = (android.os.Vibrator) getSystemService(VIBRATOR_SERVICE);
            if (v != null) v.vibrate(duration);
        }
        
        @android.webkit.JavascriptInterface
        public void keepScreenOn(boolean keep) {
            runOnUiThread(() -> {
                if (keep) {
                    getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                } else {
                    getWindow().clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                }
            });
        }
    }
}
