package io.github.garg24.comaxians.ecran

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Ouvre l'affichage à l'allumage de la box (et après une mise à jour de l'application).
 * Sur Android 10 et plus, l'ouverture automatique demande l'autorisation « afficher par-dessus
 * les autres applications » (bouton dans les réglages) ou le mode « écran d'accueil ».
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED,
            "android.intent.action.QUICKBOOT_POWERON",
            Intent.ACTION_MY_PACKAGE_REPLACED -> {
                if (!Prefs.autostart(context) || Prefs.displayUrl(context) == null) return
                try {
                    context.startActivity(
                        Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    )
                } catch (e: Exception) {
                    // Démarrage refusé par le système (restrictions d'arrière-plan) : rien de plus à faire
                }
            }
        }
    }
}
