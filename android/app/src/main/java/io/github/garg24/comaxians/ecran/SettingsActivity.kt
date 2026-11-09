package io.github.garg24.comaxians.ecran

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Réglages : adresse du serveur, nom de l'écran, options de démarrage. */
class SettingsActivity : Activity() {

    private lateinit var serverInput: EditText
    private lateinit var screenInput: EditText
    private lateinit var liteCheck: CheckBox
    private lateinit var autostartCheck: CheckBox
    private lateinit var homeCheck: CheckBox
    private lateinit var testButton: Button
    private lateinit var testResult: TextView
    private lateinit var overlayButton: Button

    private val handler = Handler(Looper.getMainLooper())
    // Réglages laissés ouverts sans action : retour automatique à l'affichage
    private val autoReturn = Runnable { if (Prefs.displayUrl(this) != null) backToDisplay() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        serverInput = findViewById(R.id.server_input)
        screenInput = findViewById(R.id.screen_input)
        liteCheck = findViewById(R.id.lite_check)
        autostartCheck = findViewById(R.id.autostart_check)
        homeCheck = findViewById(R.id.home_check)
        testButton = findViewById(R.id.test_button)
        testResult = findViewById(R.id.test_result)
        overlayButton = findViewById(R.id.overlay_button)

        serverInput.setText(Prefs.server(this).removePrefix("http://"))
        screenInput.setText(Prefs.screen(this))
        liteCheck.isChecked = Prefs.lite(this)
        autostartCheck.isChecked = Prefs.autostart(this)
        homeCheck.isChecked = Prefs.isHome(this)

        findViewById<Button>(R.id.save_button).setOnClickListener { save() }
        testButton.setOnClickListener { testConnection() }
        overlayButton.setOnClickListener { askOverlayPermission() }
        findViewById<Button>(R.id.android_settings_button).setOnClickListener {
            tryStart(Intent(Settings.ACTION_SETTINGS))
        }
        findViewById<TextView>(R.id.info_text).text = infoText()

        if (Prefs.server(this).isEmpty()) serverInput.requestFocus()
    }

    override fun onResume() {
        super.onResume()
        // Android 10+ : sans cette autorisation, l'ouverture automatique au démarrage est bloquée
        overlayButton.visibility =
            if (Build.VERSION.SDK_INT >= 29 && !Settings.canDrawOverlays(this) && !Prefs.isHome(this)) View.VISIBLE
            else View.GONE
        resetAutoReturn()
    }

    override fun onPause() {
        handler.removeCallbacks(autoReturn)
        super.onPause()
    }

    override fun onUserInteraction() {
        super.onUserInteraction()
        resetAutoReturn()
    }

    private fun resetAutoReturn() {
        handler.removeCallbacks(autoReturn)
        handler.postDelayed(autoReturn, AUTO_RETURN_MS)
    }

    private fun save() {
        val server = Prefs.normalizeServer(serverInput.text.toString())
        if (server == null) {
            serverInput.error = "Indiquez l'adresse du serveur, par exemple 192.168.1.20"
            serverInput.requestFocus()
            return
        }
        val screen = Prefs.normalizeScreen(screenInput.text.toString())
        Prefs.save(this, server, screen, liteCheck.isChecked, autostartCheck.isChecked)

        val wasHome = Prefs.isHome(this)
        Prefs.setHome(this, homeCheck.isChecked)
        if (homeCheck.isChecked && !wasHome) {
            Toast.makeText(
                this,
                "Appuyez sur la touche Accueil de la télécommande, puis choisissez « Axians Affichage » et « Toujours ».",
                Toast.LENGTH_LONG
            ).show()
            if (!tryStart(Intent(Settings.ACTION_HOME_SETTINGS))) {
                tryStart(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME))
            }
        }
        backToDisplay()
    }

    private fun backToDisplay() {
        startActivity(
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        )
        finish()
    }

    private fun testConnection() {
        val base = Prefs.normalizeServer(serverInput.text.toString())
        if (base == null) {
            testResult.text = "✖ Indiquez d'abord l'adresse du serveur."
            return
        }
        val screen = Prefs.normalizeScreen(screenInput.text.toString())
        val url = "$base/api/playlist" + if (screen.isEmpty()) "" else "?screen=" + URLEncoder.encode(screen, "UTF-8")
        testButton.isEnabled = false
        testResult.text = "Test en cours : $base …"
        Thread {
            val message = try {
                val conn = URL(url).openConnection() as HttpURLConnection
                conn.connectTimeout = 5000
                conn.readTimeout = 5000
                val code = conn.responseCode
                if (code != 200) {
                    "✖ Le serveur répond, mais avec une erreur (code $code)."
                } else {
                    val body = conn.inputStream.bufferedReader().use { it.readText() }
                    val n = JSONObject(body).getJSONArray("slides").length()
                    "✔ Serveur joignable : $n diapositive(s) à afficher sur cet écran."
                }
            } catch (e: Exception) {
                "✖ Serveur injoignable ($base) : ${e.javaClass.simpleName}" +
                    (e.message?.let { " – $it" } ?: "") +
                    "\nVérifiez l'adresse, que la box est sur le même réseau et que le serveur est allumé."
            }
            runOnUiThread {
                testResult.text = message
                testButton.isEnabled = true
            }
        }.start()
    }

    private fun askOverlayPermission() {
        val ok = tryStart(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        if (!ok) {
            Toast.makeText(
                this,
                "Option indisponible sur cette box : cochez plutôt « Utiliser comme écran d'accueil ».",
                Toast.LENGTH_LONG
            ).show()
        }
    }

    private fun tryStart(intent: Intent): Boolean = try {
        startActivity(intent); true
    } catch (e: Exception) {
        false
    }

    private fun infoText(): String {
        val major = Prefs.webViewMajor(this)
        val engine = when {
            major == 0 -> "Moteur web : version inconnue"
            major >= 111 -> "Moteur web : Chrome $major ✔ à jour"
            major >= 84 -> "Moteur web : Chrome $major ⚠ ancien : affichage simplifié (effets lumineux désactivés). " +
                "Mettez à jour « Android System WebView » si possible."
            else -> "Moteur web : Chrome $major ✖ trop ancien : mise en page dégradée. " +
                "Mettez à jour « Android System WebView » depuis le Play Store."
        }
        return "Application : version ${Prefs.appVersion(this)} · Android ${Build.VERSION.RELEASE}\n$engine"
    }

    companion object {
        private const val AUTO_RETURN_MS = 180_000L
    }
}
