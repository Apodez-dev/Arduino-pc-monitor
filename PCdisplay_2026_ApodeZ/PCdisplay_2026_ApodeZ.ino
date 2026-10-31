/*
  Мониторинг ресурсов ПК для LCD дисплея [Final Safe Edition]
  - Главный экран со всеми ключевыми параметрами ПК
  - Автоотключение подсветки через 30 сек после потери связи
  - Защита от перелива символов при 100%
*/

// ------------------------ НАСТРОЙКИ ----------------------------
#define DRIVER_VERSION 1    // 0 - маркировка драйвера кончается на 4АТ, 1 - на 4Т
#define CPU_TEMP_SENSOR 0   // 0 или 1, выбрать перебором датчик температуры CPU
#define ERROR_BACKLIGHT 0   // 0 - гасить подсветку при потере сигнала, 1 - не гасить
// ------------------------ НАСТРОЙКИ ----------------------------

int t_cpu;

// -------------------- БИБЛИОТЕКИ ---------------------
#include <string.h>             
#include <Wire.h>               
#include <LiquidCrystal_I2C.h>  
// -------------------- БИБЛИОТЕКИ ---------------------

// -------- АВТОВЫБОР ОПРЕДЕЛЕНИЯ ДИСПЛЕЯ -------------
#if (DRIVER_VERSION)
LiquidCrystal_I2C lcd(0x27, 20, 4);
#else
LiquidCrystal_I2C lcd(0x3f, 20, 4);
#endif
// -------- АВТОВЫБОР ОПРЕДЕЛЕНИЯ ДИСПЛЕЯ -------------

#if (CPU_TEMP_SENSOR)
int CPUtemp = 19;
#else
int CPUtemp = 0;
#endif

// Символы для графиков полос (барграфов)
byte right_empty[8] = {0b11111,  0b00001,  0b00001,  0b00001,  0b00001,  0b00001,  0b00001,  0b11111};
byte left_empty[8] = {0b11111,  0b10000,  0b10000,  0b10000,  0b10000,  0b10000,  0b10000,  0b11111};
byte center_empty[8] = {0b11111, 0b00000,  0b00000,  0b00000,  0b00000,  0b00000,  0b00000,  0b11111};
byte bar2[] = {0b11111, 0b11000, 0b11000, 0b11000, 0b11000, 0b11000, 0b11000, 0b11111};
byte bar3[] = {  B11111,  B11100,  B11100,  B11100,  B11100,  B11100,  B11100,  B11111};
byte bar4[] = {  B11111,  B11110,  B11110,  B11110,  B11110,  B11110,  B11110,  B11111};
byte rightbar1[] = {  B11111,  B10001,  B10001,  B10001,  B10001,  B10001,  B10001,  B11111};
byte rightbar3[] = {  B11111,  B11101,  B11101,  B11101,  B11101,  B11101,  B11101,  B11111};

// Кастомный символ: маленькое сердечко (ячейка 0)
byte heart_icon[8] = {0b00000, 0b01010, 0b11111, 0b11111, 0b01110, 0b00100, 0b00000, 0b00000};

char inData[120];        
int PCdata[20];         
byte index = 0;
unsigned long timeout;
boolean reDraw_flag = 1, updateDisplay_flag, timeOut_flag = 1;
char buf[10];           

// Переменные для заставки
bool logoFinished = false;
unsigned long logoStartTime = 0;

void setup() {
  Serial.begin(9600);
  
  // Инициализация дисплея
  lcd.init();
  lcd.backlight();         
  lcd.clear();             
  
  show_logo_apodez();      
  logoStartTime = millis();
}

// ------------------------------ ОСНОВНОЙ ЦИКЛ -------------------------------
void loop() {
  // Показ стартового экрана в течение 5 секунд
  if (!logoFinished) {
    if (millis() - logoStartTime >= 5000) {
      logoFinished = true;
      lcd.clear();             
      initBar3();
      draw_labels_1(); // Рисуем статические метки один раз при старте
    }
    return;
  }

  parsing();                   
  updateDisplay();             
  timeoutTick();               
}
// ------------------------------ ОСНОВНОЙ ЦИКЛ -------------------------------

void initBar3() {
  lcd.createChar(0, left_empty);
  lcd.createChar(1, center_empty);
  lcd.createChar(2, right_empty);
  lcd.createChar(3, bar2);
  lcd.createChar(4, bar3);
  lcd.createChar(5, bar4);
  lcd.createChar(6, rightbar1);
  lcd.createChar(7, rightbar3);
}

void fillBar3(byte start_pos, byte row, byte bar_length, byte fill_percent) {
  byte infill = bar_length * fill_percent / 10;
  byte fract = infill % 10;
  infill = infill / 10;

  lcd.setCursor(start_pos, row);
  if (infill == 0) {
    if (fract >= 0 && fract < 2) lcd.write(0);
    else if (fract >= 2 && fract < 4) lcd.write(0);
    else if (fract >= 4 && fract < 6) lcd.write(3);
    else if (fract >= 6 && fract < 8) lcd.write(4);
    else if (fract >= 8) lcd.write(5);
  }
  else lcd.write(255);
  for (int n = 1; n < bar_length - 1; n++) {
    if (n < infill) lcd.write(255);
    if (n == infill) {
      if (fract >= 0 && fract < 2) lcd.write(1);
      else if (fract >= 2 && fract < 4) lcd.write(0);
      else if (fract >= 4 && fract < 6) lcd.write(3);
      else if (fract >= 6 && fract < 8) lcd.write(4);
      else if (fract >= 8) lcd.write(5);
    }
    if (n > infill) lcd.write(1);
  }
  if (infill == bar_length - 1) {
    if (fract >= 0 && fract < 2) lcd.write(2);
    else if (fract >= 2 && fract < 4) lcd.write(6);
    else if (fract >= 4 && fract < 6) lcd.write(6);
    else if (fract >= 6 && fract < 8) lcd.write(7);
    else if (fract >= 8) lcd.write(255);
  }
  else if (infill == bar_length) lcd.write(255);
  else lcd.write(2);
}

// Защищенный парсер пакетов от ПК
void parsing() {
  while (Serial.available() > 0) {
    char aChar = Serial.read();
    if (aChar != 'E') {
      if (index < 120) { 
        inData[index] = aChar;
        index++;
        inData[index] = '\0';
      } else {
        index = 0; 
      }
    } else {
      char *p = inData;
      char *str;
      index = 0;
      while ((str = strtok_r(p, ";", &p)) != NULL) {
        PCdata[index] = atoi(str);
        index++;
      }
      index = 0;
      updateDisplay_flag = 1;
    }
    
    // Если связь есть — держим подсветку включенной
    if (!timeOut_flag) {
      if (!ERROR_BACKLIGHT) lcd.backlight();
    }
    
    timeout = millis();
    timeOut_flag = 1;
  }
}

void updateDisplay() {
  if (updateDisplay_flag) {
    if (reDraw_flag) {
      lcd.clear();
      draw_labels_1();
      reDraw_flag = 0;
    }
    
    draw_stats_1();
    updateDisplay_flag = 0;
  }
}

void draw_stats_1() {
  t_cpu = 100 - PCdata[CPUtemp];
  
  // Температура CPU
  lcd.setCursor(4, 0); 
  lcd.print(t_cpu); 
  lcd.write(223); 
  if (t_cpu < 10) lcd.print(" ");
  if (t_cpu < 100) lcd.print(" ");

  // Загрузка CPU (%) с защитой от перелива на 100%
  lcd.setCursor(17, 0); 
  if (PCdata[4] >= 100) {
    lcd.print("100");
  } else {
    sprintf(buf, "%d%%", PCdata[4]);
    lcd.print(buf);
    if (PCdata[4] < 10) lcd.print(" ");
  }

  // Температура GPU
  lcd.setCursor(4, 1); 
  lcd.print(PCdata[1]); 
  lcd.write(223); 
  if (PCdata[1] < 10) lcd.print(" ");
  if (PCdata[1] < 100) lcd.print(" ");

  // Загрузка GPU (%) с защитой от перелива на 100%
  lcd.setCursor(17, 1); 
  if (PCdata[5] >= 100) {
    lcd.print("100");
  } else {
    sprintf(buf, "%d%%", PCdata[5]);
    lcd.print(buf);
    if (PCdata[5] < 10) lcd.print(" ");
  }

  // GPUmem (%) с защитой от перелива на 100%
  lcd.setCursor(17, 2); 
  if (PCdata[7] >= 100) {
    lcd.print("100");
  } else {
    sprintf(buf, "%d%%", PCdata[7]);
    lcd.print(buf);
    if (PCdata[7] < 10) lcd.print(" ");
  }

  // RAMuse (%) с защитой от перелива на 100%
  lcd.setCursor(17, 3); 
  if (PCdata[6] >= 100) {
    lcd.print("100");
  } else {
    sprintf(buf, "%d%%", PCdata[6]);
    lcd.print(buf);
    if (PCdata[6] < 10) lcd.print(" ");
  }

  // Шкалы (барграфы)
  fillBar3(7, 0, 10, PCdata[4]);
  fillBar3(7, 1, 10, PCdata[5]);
  fillBar3(7, 2, 10, PCdata[7]);
  fillBar3(7, 3, 10, PCdata[6]);
}

void draw_labels_1() {
  lcd.setCursor(0, 0); lcd.print("CPU:");
  lcd.setCursor(0, 1); lcd.print("GPU:");
  lcd.setCursor(0, 2); lcd.print("GPUmem:");
  lcd.setCursor(0, 3); lcd.print("RAMuse:");
}

void timeoutTick() {
  if ((millis() - timeout > 5000) && timeOut_flag) {
    lcd.clear();
    lcd.setCursor(5, 1);
    lcd.print("CONNECTION");
    lcd.setCursor(7, 2);
    lcd.print("FAILED");
    timeOut_flag = 0;
    reDraw_flag = 1;
  }
  
  if (!timeOut_flag && (millis() - timeout > 35000)) {
    if (!ERROR_BACKLIGHT) lcd.noBacklight();   
  }
}

void show_logo_apodez() {
  lcd.createChar(0, heart_icon);

  lcd.setCursor(5, 0);
  lcd.print("Powered by");

  lcd.setCursor(7, 1);
  lcd.print("ApodeZ");

  lcd.setCursor(1, 2);
  lcd.print("Press F to respect");

  lcd.setCursor(5, 3);
  lcd.write(byte(0));
  lcd.print("AlexGyver!");
  lcd.write(byte(0)); 
}
