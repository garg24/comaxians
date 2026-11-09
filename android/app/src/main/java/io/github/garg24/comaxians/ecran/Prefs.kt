package io.github.garg24.comaxians.ecran

import android.content.ComponentName
import android.content.Context
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.net.Uri
import android.webkit.WebSettings
import java.net.URLEncoder

/** Réglages de l'écran, mémorisés sur la box. */
object Prefs {
    private const val FILE = "ecran"
    const val DEFAULT_PORT = 8080

    private fun sp(c: Context): SharedPreferences = c.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun server(c: Context): String = sp(c).getString("server", "") ?: ""
    fun screen(c: Context): String = sp(c).getString("screen", "") ?: ""
    fun lite(c: Context): Boolean = sp(c).getBoolean("lite", false)
    fun autostart(c: Context): Boolean = sp(c).getBoolean("autostart", true)

    fun save(c: Context, server: String, screen: String, lite: Boolean, autostart: Boolean) {
        sp(c).edit()
            .putString("server", server)
            .putString("screen", screen)
            .putBoolean("lite", lite)
            .putBoolean("autostart", autostart)
            .apply()
    }

    /** « 192.168.1.20 », « 192.168.1.20:8080 », « http://pi.local:8080/display » → « http://192.168.1.20:8080 » */
    fun normalizeServer(raw: String): String? {
        var s = raw.trim().trimEnd('/')
        if (s.isEmpty()) return null
        if (!s.startsWith("http://", true) && !s.startsWith("https://", true)) s = "http://$s"
        val uri = Uri.parse(s)
        val host = uri.host ?: return null
        if (host.isBlank() || host.contains(' ')) return null
        val scheme = (uri.scheme ?: "http").lowercase()
        val port = if (uri.port > 0) uri.port else if (scheme == "http") DEFAULT_PORT else -1
        return if (port > 0) "$scheme://$host:$port" else "$scheme://$host"
    }

    /** Nom d'écran accepté par le serveur : lettres, chiffres, - et _ (40 caractères max). */
    fun normalizeScreen(raw: String): String =
        raw.trim().lowercase().replace(Regex("\\s+"), "-").replace(Regex("[^a-z0-9_-]"), "").take(40)

    /** Adresse complète de la page affichée, ou null si le serveur n'est pas encore réglé. */
    fun displayUrl(c: Context): String? {
        val base = normalizeServer(server(c)) ?: return null
        val query = mutableListOf<String>()
        val name = screen(c)
        if (name.isNotEmpty()) query += "screen=" + URLEncoder.encode(name, "UTF-8")
        if (lite(c)) query += "lite=1"
        return "$base/display" + if (query.isEmpty()) "" else "?" + query.joinToString("&")
    }

    // ------------------------------------------------------------ mode « écran d'accueil »
    private fun homeAlias(c: Context) = ComponentName(c, "${c.packageName}.HomeAlias")

    fun isHome(c: Context): Boolean =
        c.packageManager.getComponentEnabledSetting(homeAlias(c)) == PackageManager.COMPONENT_ENABLED_STATE_ENABLED

    fun setHome(c: Context, enabled: Boolean) {
        c.packageManager.setComponentEnabledSetting(
            homeAlias(c),
            if (enabled) PackageManager.COMPONENT_ENABLED_STATE_ENABLED
            else PackageManager.COMPONENT_ENABLED_STATE_DISABLED,
            PackageManager.DONT_KILL_APP
        )
    }

    // ------------------------------------------------------------ moteur web
    /** Version majeure de Chrome utilisée par la WebView de la box (0 si inconnue). */
    fun webViewMajor(c: Context): Int = try {
        Regex("Chrome/(\\d+)").find(WebSettings.getDefaultUserAgent(c))?.groupValues?.get(1)?.toInt() ?: 0
    } catch (e: Exception) {
        0
    }

    fun appVersion(c: Context): String = try {
        c.packageManager.getPackageInfo(c.packageName, 0).versionName ?: "?"
    } catch (e: Exception) {
        "?"
    }
}
