package io.github.garg24.comaxians.ecran

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.util.Log
import android.webkit.ConsoleMessage
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.TextView

/**
 * Affiche en plein écran la page /display du serveur Axians.
 * - écran toujours allumé, barres d'Android masquées ;
 * - nouvel essai automatique si le serveur ne répond pas (box allumée avant le réseau…) ;
 * - télécommande : Retour ou Menu = réglages, flèches = navigation entre diapositives.
 * Réglage possible sans écran tactile, via adb :
 *   adb shell am start -n io.github.garg24.comaxians.ecran/.MainActivity --es server 192.168.1.20 --es screen hall
 */
class MainActivity : Activity() {

    private lateinit var root: FrameLayout
    private lateinit var status: TextView
    private var web: WebView? = null
    private var loadedUrl: String? = null
    private var mainFrameError = false
    private val handler = Handler(Looper.getMainLooper())
    private val retry = Runnable { load(force = true) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyIntentConfig(intent)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        root = FrameLayout(this).apply { setBackgroundColor(FOND) }
        status = TextView(this).apply {
            setTextColor(Color.WHITE)
            textSize = 20f
            gravity = Gravity.CENTER
            setPadding(64, 64, 64, 64)
            setBackgroundColor(FOND)
            visibility = View.GONE
        }
        setContentView(root)
        createWebView()
        root.addView(status, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (applyIntentConfig(intent)) load(force = true)
    }

    override fun onResume() {
        super.onResume()
        hideSystemUi()
        val url = Prefs.displayUrl(this)
        if (url == null) {
            // Premier lancement : rien n'est réglé
            startActivity(Intent(this, SettingsActivity::class.java))
            return
        }
        load(force = url != loadedUrl)
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        web?.destroy()
        super.onDestroy()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemUi()
    }

    // ------------------------------------------------------------------ télécommande
    @Deprecated("Gestion simple de la touche Retour, compatible Android 5 à 15")
    override fun onBackPressed() {
        openSettings()
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_MENU || keyCode == KeyEvent.KEYCODE_SETTINGS) {
            openSettings()
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    private fun openSettings() {
        startActivity(Intent(this, SettingsActivity::class.java))
    }

    // ------------------------------------------------------------------ affichage
    @SuppressLint("SetJavaScriptEnabled")
    private fun createWebView() {
        web?.let { root.removeView(it); it.destroy() }
        val w = WebView(this)
        w.setBackgroundColor(FOND)
        with(w.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true                    // cache des contenus en cas de coupure réseau
            mediaPlaybackRequiresUserGesture = false    // vidéos lues automatiquement
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            cacheMode = WebSettings.LOAD_DEFAULT
            // La page gère elle-même sa mise à l'échelle (canevas 1920 x 1080) : on respecte sa balise
            // « viewport », sans zoom ni agrandissement des polices imposé par l'accessibilité d'Android
            useWideViewPort = true
            // pas de « vue d'ensemble » : elle élargirait la zone de mise en page au contenu (1920 px)
            loadWithOverviewMode = false
            textZoom = 100
            setSupportZoom(false)
            builtInZoomControls = false
            displayZoomControls = false
            userAgentString = "$userAgentString ComAxiansEcran/${Prefs.appVersion(this@MainActivity)}"
        }
        w.webChromeClient = object : WebChromeClient() {
            // Messages de la page (console JavaScript) visibles avec « adb logcat -s ComAxians »
            override fun onConsoleMessage(message: ConsoleMessage): Boolean {
                Log.i(TAG, "console: ${message.message()} (${message.sourceId()}:${message.lineNumber()})")
                return true
            }
        }
        w.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView, url: String?) {
                if (!mainFrameError) status.visibility = View.GONE
                // Diagnostic (adb logcat -s ComAxians) : zone visible et place occupée par la diapositive
                logViewport(view)
                handler.postDelayed({ logViewport(view) }, 5000)
            }

            @Deprecated("Appelée pour la page principale sur toutes les versions d'Android")
            override fun onReceivedError(view: WebView?, errorCode: Int, description: String?, failingUrl: String?) {
                mainFrameError = true
                showStatus(
                    "Connexion au serveur impossible\n${Prefs.normalizeServer(Prefs.server(this@MainActivity))}\n\n" +
                        "Nouvel essai dans ${RETRY_MS / 1000} secondes…\n\n" +
                        "Touche Retour de la télécommande : réglages"
                )
                handler.removeCallbacks(retry)
                handler.postDelayed(retry, RETRY_MS)
            }

            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                // Le moteur web a planté (mémoire…) : on le recrée au lieu de fermer l'application
                createWebView()
                load(force = true)
                return true
            }
        }
        root.addView(w, 0, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        w.isFocusable = true
        w.isFocusableInTouchMode = true
        w.requestFocus()
        web = w
    }

    private fun load(force: Boolean) {
        val url = Prefs.displayUrl(this) ?: return
        val w = web ?: return
        if (!force && url == loadedUrl) return
        handler.removeCallbacks(retry)
        mainFrameError = false
        if (loadedUrl == null) showStatus("Connexion à l'écran Axians…\n$url")
        loadedUrl = url
        w.loadUrl(url)
        w.requestFocus()
    }

    private fun logViewport(view: WebView) {
        view.evaluateJavascript(
            "(function(){var v=window.visualViewport,s=document.getElementById('stage');" +
                "var r=s?s.getBoundingClientRect():{left:0,top:0,width:0,height:0};" +
                "return JSON.stringify({w:innerWidth,h:innerHeight,vw:v?v.width:innerWidth,vh:v?v.height:innerHeight," +
                "dpr:devicePixelRatio,stage:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)]," +
                "ua:(navigator.userAgent.match(/Chrome\\/[\\d.]+/)||[''])[0]});})()"
        ) { Log.i(TAG, "viewport $it") }
    }

    private fun showStatus(text: String) {
        status.text = text
        status.visibility = View.VISIBLE
    }

    /** Réglages transmis par adb (--es server …, --es screen …, --ez lite true). Renvoie true si modifiés. */
    private fun applyIntentConfig(i: Intent?): Boolean {
        val server = i?.getStringExtra("server") ?: return false
        val screen = i.getStringExtra("screen") ?: Prefs.screen(this)
        val lite = if (i.hasExtra("lite")) i.getBooleanExtra("lite", false) else Prefs.lite(this)
        Prefs.save(this, server, Prefs.normalizeScreen(screen), lite, Prefs.autostart(this))
        return true
    }

    @Suppress("DEPRECATION")
    private fun hideSystemUi() {
        if (Build.VERSION.SDK_INT >= 30) {
            window.setDecorFitsSystemWindows(false)
            window.insetsController?.let {
                it.hide(WindowInsets.Type.systemBars())
                it.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            window.decorView.systemUiVisibility = (View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                or View.SYSTEM_UI_FLAG_FULLSCREEN
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN)
        }
    }

    companion object {
        private const val TAG = "ComAxians"
        private const val FOND = 0xFF070B18.toInt()
        private const val RETRY_MS = 15_000L
    }
}
