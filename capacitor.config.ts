import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.joko.image',
  appName: 'joko-image',
  webDir: 'dist',
  server: {
    url: 'https://image.get-money.locker',
    cleartext: false,
  },
};

export default config;
