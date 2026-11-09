plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Numéro de version : numéro de compilation GitHub Actions (1 en local)
val buildNumber = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
// Clé de signature fournie par GitHub Actions (secret) ; sinon clé de test (debug)
val keystorePath: String? = System.getenv("ECRAN_KEYSTORE")

android {
    namespace = "io.github.garg24.comaxians.ecran"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.garg24.comaxians.ecran"
        minSdk = 21          // Android 5.0 : couvre la quasi-totalité des box Android TV
        targetSdk = 35
        versionCode = buildNumber
        versionName = "1.0.$buildNumber"
    }

    signingConfigs {
        if (keystorePath != null) {
            create("release") {
                storeFile = file(keystorePath)
                storeType = "pkcs12"
                storePassword = System.getenv("ECRAN_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ECRAN_KEY_ALIAS") ?: "comaxians"
                keyPassword = System.getenv("ECRAN_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (keystorePath != null) signingConfigs.getByName("release")
                            else signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}
