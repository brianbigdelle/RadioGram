#include <Inkplate.h>
#include <Arduino.h>

// ===================== Inkplate Setup =====================
Inkplate display(INKPLATE_1BIT);
static const uint32_t BAUD = 115200;
static const int ROTATION = 2; // Adjust if your screen is upside down
static const int W = 1024, H = 758;

// ===================== Terminal Config ====================
static const int MARGIN = 10;
static const int FONT_SIZE = 2; 
static const int LINE_HEIGHT = 24; 
static const int KEYBOARD_H = 300; 
static const int INPUT_BAR_H = 50; 

// Calculate max lines based on available screen space
static const int TERMINAL_H = H - KEYBOARD_H - INPUT_BAR_H - (2 * MARGIN);
static const int MAX_LINES = TERMINAL_H / LINE_HEIGHT;

// Buffers
String terminal_lines[30]; 
String input_buffer = "";

// ===================== KEYBOARD LAYOUT =====================
// 5 Rows: 4 for characters, 1 for control
// 40 Characters total for the grid
// UPDATED: Lowercase letters and replaced '-' with ':'
const char* KB_LAYOUT = "1234567890qwertyuiopasdfghjklzxcvbnm,.:_"; 
const int KB_COLS = 10;
const int KB_ROWS = 5; // 4 Text Rows + 1 Control Row

// ===================== Serial Protocol Helpers ======================

void send_txin_frame(const String &text) {
  if (text.length() == 0) return;
  uint16_t L = (uint16_t)min((int)text.length(), 60000);
  
  Serial.write('T'); Serial.write('X'); Serial.write('I'); Serial.write('N');
  Serial.write((uint8_t)(L & 0xFF)); Serial.write((uint8_t)(L >> 8));
  Serial.write((const uint8_t*)text.c_str(), L);
  Serial.flush();
}

static bool read_exact(uint8_t* buf, size_t len, uint32_t to_ms=1000) {
  size_t got = 0; unsigned long t0 = millis();
  while (got < len) {
    if (Serial.available()) { 
      int c = Serial.read(); 
      if (c>=0) buf[got++] = (uint8_t)c; 
    }
    else { 
      if (millis() - t0 > to_ms) return false; 
      delay(1); 
    }
  }
  return true;
}

// ===================== Logic & State ======================

void push_terminal_line(String line) {
  for (int i = 0; i < MAX_LINES - 1; i++) {
    terminal_lines[i] = terminal_lines[i+1];
  }
  terminal_lines[MAX_LINES - 1] = line;
}

// ===================== Drawing ======================

void draw_keyboard() {
  int k_start_y = H - KEYBOARD_H;
  int key_w = W / KB_COLS;
  int key_h = KEYBOARD_H / KB_ROWS; // Split height by 5 rows

  display.setTextSize(2);
  display.setTextColor(BLACK, WHITE);

  // 1. Draw The 40 Standard Keys (Rows 0-3)
  int len = strlen(KB_LAYOUT);
  for (int i = 0; i < len; i++) {
    int r = i / KB_COLS;
    int c = i % KB_COLS;
    
    int x = c * key_w;
    int y = k_start_y + (r * key_h);

    display.drawRect(x, y, key_w, key_h, BLACK);
    
    char keyChar = KB_LAYOUT[i];
    display.setCursor(x + (key_w/2) - 6, y + (key_h/2) - 8); 
    display.print(keyChar);
  }

  // 2. Draw Control Row (Row 4 - The Bottom)
  int y_ctrl = k_start_y + (4 * key_h);
  
  // Backspace (Left, 3 Cols wide)
  int w_bksp = key_w * 3;
  display.drawRect(0, y_ctrl, w_bksp, key_h, BLACK);
  display.setCursor(w_bksp/2 - 20, y_ctrl + (key_h/2) - 8);
  display.print("BKSP");

  // Space (Middle, 4 Cols wide)
  int w_space = key_w * 4;
  int x_space = w_bksp;
  display.drawRect(x_space, y_ctrl, w_space, key_h, BLACK);
  display.setCursor(x_space + w_space/2 - 30, y_ctrl + (key_h/2) - 8);
  display.print("SPACE");

  // Enter (Right, 3 Cols wide)
  int w_enter = key_w * 3;
  int x_enter = x_space + w_space;
  display.drawRect(x_enter, y_ctrl, w_enter, key_h, BLACK);
  display.setCursor(x_enter + w_enter/2 - 25, y_ctrl + (key_h/2) - 8);
  display.print("ENTER");
}

void draw_input_bar() {
  int y = H - KEYBOARD_H - INPUT_BAR_H;
  display.fillRect(0, y, W, INPUT_BAR_H, WHITE);
  display.drawRect(0, y, W, INPUT_BAR_H, BLACK);
  
  display.setTextSize(2);
  display.setCursor(MARGIN, y + 15);
  display.print("> " + input_buffer + "_");
}

void draw_terminal_log() {
  display.fillRect(0, 0, W, TERMINAL_H, WHITE);
  
  display.setTextSize(FONT_SIZE);
  display.setTextColor(BLACK, WHITE);
  
  for (int i = 0; i < MAX_LINES; i++) {
    if (terminal_lines[i].length() > 0) {
      display.setCursor(MARGIN, MARGIN + (i * LINE_HEIGHT));
      display.print(terminal_lines[i]);
    }
  }
}

void redraw_full() {
  display.clearDisplay();
  draw_terminal_log();
  draw_input_bar();
  draw_keyboard();
  display.display();
}

void redraw_partial_input() {
  draw_input_bar();
  display.partialUpdate();
}

void redraw_partial_log() {
  draw_terminal_log();
  display.partialUpdate();
}

// ===================== Input Handling ======================

void handle_touch(int x, int y) {
  int k_start_y = H - KEYBOARD_H;
  
  // Check if touch is inside Keyboard Area
  if (y >= k_start_y) {
    int key_h = KEYBOARD_H / KB_ROWS;
    int row = (y - k_start_y) / key_h;
    
    // Rows 0-3: Character Keys
    if (row < 4) {
      int key_w = W / KB_COLS;
      int col = x / key_w;
      int charIndex = (row * KB_COLS) + col;
      
      if (charIndex < strlen(KB_LAYOUT)) {
        input_buffer += KB_LAYOUT[charIndex];
        redraw_partial_input();
      }
    }
    // Row 4: Control Keys (BKSP, SPACE, ENTER)
    else {
      int key_w = W / KB_COLS;
      
      // BKSP covers cols 0-2 (approx)
      if (x < key_w * 3) {
        if (input_buffer.length() > 0) {
          input_buffer.remove(input_buffer.length() - 1);
          redraw_partial_input();
        }
      } 
      // SPACE covers cols 3-6 (approx)
      else if (x < key_w * 7) {
        input_buffer += ' ';
        redraw_partial_input();
      } 
      // ENTER covers cols 7-9
      else {
        if (input_buffer.length() > 0) {
          send_txin_frame(input_buffer); 
          push_terminal_line("> " + input_buffer); 
          input_buffer = ""; 
          redraw_full(); 
        }
      }
    }
  }
}

// ===================== Arduino Setup & Loop ======================

void setup() {
  Serial.begin(BAUD);
  display.begin();
  display.setRotation(ROTATION);
  
  if (!display.tsInit(true)) {
    Serial.println("Touch Init Failed");
  }
  
  push_terminal_line("--- INKPLATE TERMINAL ---");
  push_terminal_line("Waiting for Reticulum...");
  redraw_full();
}

void loop() {
  // 1. Check Touch
  if (display.tsAvailable()) {
    uint16_t x[2], y[2];
    uint8_t n = display.tsGetData(x, y);
    if (n) handle_touch(x[0], y[0]);
  }

  // 2. Check Serial (TXTP frames from RPi)
  if (Serial.available() >= 4) {
    uint8_t hdr[4];
    if (Serial.peek() != 'T') { 
       Serial.read(); return; 
    }
    
    if (!read_exact(hdr, 4)) return;

    if (memcmp(hdr, "TXTP", 4) == 0) {
      uint8_t lb[2];
      if (!read_exact(lb, 2)) return;
      uint16_t L = lb[0] | (lb[1] << 8);

      if (L > 0 && L <= 60000) {
        uint8_t* b = (uint8_t*)malloc(L + 1);
        if (b && read_exact(b, L)) {
          b[L] = 0;
          String incoming = String((char*)b);
          
          int start = 0;
          int pos = incoming.indexOf('\n');
          while(pos >= 0) {
             push_terminal_line(incoming.substring(start, pos));
             start = pos + 1;
             pos = incoming.indexOf('\n', start);
          }
          if(start < incoming.length()) {
             push_terminal_line(incoming.substring(start));
          }
          
          redraw_partial_log(); 
        }
        if (b) free(b);
      }
    } else {
      while(Serial.available()) Serial.read();
    }
  }
}