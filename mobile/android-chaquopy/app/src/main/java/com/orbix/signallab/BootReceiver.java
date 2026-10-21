package com.orbix.signallab;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import java.util.concurrent.TimeUnit;

/**
 * BootReceiver - Restart 24/7 collection after device reboot
 * Ensures APK keeps running even after restart
 */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) {
            PeriodicWorkRequest work = new PeriodicWorkRequest.Builder(
                OrbixWorker.class, 15, TimeUnit.MINUTES
            ).build();
            WorkManager.getInstance(context).enqueue(work);
        }
    }
}
