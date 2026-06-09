from PIL import Image, ImageDraw, ImageFont
import customtkinter as ctk
import random
import math

class CTkPieChart(ctk.CTkLabel):
    """
    A customtkinter widget for pie chart display.
    Author: Akascape
    Version: 0.1
    """
    
    def __init__(self,
                 master,
                 command=None,
                 values={},
                 **kwargs):

        self.arc = None
        self.im = Image.new('RGBA', (1000, 1000))
        
        self.size = kwargs.get('radius') or 200
        self.background = kwargs.get('bg_color') or master.cget("fg_color")
        self.border_width = kwargs.get('border_width') or 0
        self.border_color = kwargs.get('border_color') or ctk.ThemeManager.theme["CTkButton"]["border_color"]
        self.width = kwargs.get('line_width') or 20
        self.text_color = kwargs.get('text_color') or None
        # New parameters for segment borders
        self.segment_border_width = kwargs.get('segment_border_width') or 0
        self.segment_border_color = kwargs.get('segment_border_color') or "black"
        # Text display mode: "percentage", "custom", or "none"
        self.text_mode = kwargs.get('text_mode') or "percentage"
        self.widget = master
        self.command = command
        self.bg = master.cget("fg_color")
        self.values = {}  # Ensure this is set before any draw/add
        for i in values:
            self.add(tag=i, draw=False, **values[i])
        super().__init__(master, image=self.arc, fg_color=self.background, compound='center', text="")
        self.draw_pie_chart()
        
    def _set_scaling(self, *args, **kwargs):
        super()._set_scaling(*args, **kwargs)

        self.size = int(self._apply_widget_scaling(self.size))
        self.width = int(self._apply_widget_scaling(self.width))
        self.border_width = int(self._apply_widget_scaling(self.border_width))
        self.segment_border_width = int(self._apply_widget_scaling(self.segment_border_width))
        
    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        
    def draw_pie_chart(self, *args):
        
        width = self.width *10
        del self.im
        self.im = Image.new('RGBA', (1000, 1000))
        draw = ImageDraw.Draw(self.im)
        
        # Don't draw the outer border here - we'll draw it after all segments
        new_angle = -90
        sum_ = 0
        
        for i in self.values.values():
            sum_ += i["value"]
            
        for value in self.values.values():
            old_angle = new_angle
            new_angle = old_angle + (value['value']/sum_) * 360
                
            # Draw the main segment - use full size regardless of border_width
            draw.arc((0, 0, 990, 990), old_angle, new_angle, value['color'], width)
            
            # Draw segment border if enabled - only from edge to center
            if self.segment_border_width > 0:
                border_color = self.widget._apply_appearance_mode(self.segment_border_color)
                
                # Calculate center point
                center_x, center_y = 495, 495
                
                # For filled pie charts, draw borders from outer edge to center
                outer_radius = 495  # Use full radius, not affected by border_width
                
                # Draw border from outer edge to center for start angle
                start_x = center_x + outer_radius * math.cos(math.radians(old_angle))
                start_y = center_y + outer_radius * math.sin(math.radians(old_angle))
                end_x = center_x
                end_y = center_y
                draw.line([(start_x, start_y), (end_x, end_y)], fill=border_color, width=self.segment_border_width)
                
                # Draw border from outer edge to center for end angle
                start_x = center_x + outer_radius * math.cos(math.radians(new_angle))
                start_y = center_y + outer_radius * math.sin(math.radians(new_angle))
                end_x = center_x
                end_y = center_y
                draw.line([(start_x, start_y), (end_x, end_y)], fill=border_color, width=self.segment_border_width)
            
            # Text handling - only draw labels for non-zero values
            if self.text_mode != "none" and value['value'] > 0:
                midpoint_angle = (old_angle + new_angle)/2
                
                xn = yn = 450  # Use full size for text positioning
                radians = 495
                arc_pos = radians / 3
                textpos = arc_pos/1.5
                perc = int(round(value['value']/sum_ * 100))
                
                # Use a proportional radius for label placement (e.g., 60% of the full radius)
                label_radius = 0.6 * 495  # 60% of the full radius
                midpoint1_x = xn + label_radius * math.cos(math.radians(midpoint_angle))
                midpoint1_y = yn + label_radius * math.sin(math.radians(midpoint_angle))

                # Determine text to display
                if self.text_mode == "percentage":
                    text_to_show = f"{perc}%"
                elif self.text_mode == "custom" and 'custom_text' in value and value['custom_text'] is not None:
                    text_to_show = value['custom_text']
                else:
                    text_to_show = f"{perc}%"
                
                # Calculate font size based on chart size - smaller now
                font_size = min(80, int(self.size * 0.65))
                
                # Draw a cross at the true center
                center_x, center_y = 500, 500
                draw.line([(center_x-10, center_y), (center_x+10, center_y)], fill="blue", width=2)
                draw.line([(center_x, center_y-10), (center_x, center_y+10)], fill="blue", width=2)

                # Draw the label text centered at the calculated position
                usable_radius = 495 - self.border_width / 2  # Account for border width
                label_radius = 0.8 * usable_radius  # 80% of usable radius
                px = center_x + label_radius * math.cos(math.radians(midpoint_angle))
                py = center_y + label_radius * math.sin(math.radians(midpoint_angle))
                font = ImageFont.load_default(size=font_size)
                draw.text((px, py), text=text_to_show, fill=value['text_color'], font=font, anchor="mm")
            
        # Draw the complete circle border around the entire pie chart AFTER all segments
        # For filled pie charts, draw border around the entire chart area
        border_thickness = self.border_width  # Use the actual border_width, no minimum
        
        # Only draw border if border_thickness > 0
        if border_thickness > 0:
            # Draw outer border around the entire pie chart - use full size
            draw.arc((0, 0, 990, 990), 0, 360,
                     self.widget._apply_appearance_mode(self.border_color), border_thickness)
        
        # Removed inner circle border to avoid double border effect
        
        self.arc = ctk.CTkImage(self.im.resize((self.size, self.size), Image.LANCZOS), size=(self.size, self.size))

        super().configure(image=self.arc)

        
    def add(self, tag, value, color=None, text_color=None, custom_text=None, draw=True):
        
        if tag in self.values:
            self.update(tag, value, color, text_color, custom_text)
            return
        
        if color is None:
            color = "#"+''.join([random.choice('ABCDEF0123456789') for i in range(6)])
            
        if text_color is None:
            if self.is_color_too_bright(color):
                text_color = "black"
            else:
                text_color = "white"
            if self.text_color:
                text_color = self.text_color
                
        self.values.update({tag:{'color': color, 'value': value, 'text_color': text_color, 'custom_text': custom_text}})
        
        if draw:
            self.draw_pie_chart()

    def delete(self, tag):
        if tag in self.values:
            del self.values[tag]
        self.draw_pie_chart()

    def update(self, tag, value=None, color=None, text_color=None, custom_text=None):
        if tag in self.values:
            if value:
                self.values[tag]['value'] = value
            if color:
                self.values[tag]['color'] = color
            if text_color:
                self.values[tag]['text_color'] = text_color
            if custom_text is not None:
                self.values[tag]['custom_text'] = custom_text
            self.draw_pie_chart()       
        super().update()
        
    def cget(self, param):
        if param=="bg_color":
            return self.background
        if param=="border_color":
            return self.border_color
        if param=="border_width":
            return self.border_width
        if param=="segment_border_width":
            return self.segment_border_width
        if param=="segment_border_color":
            return self.segment_border_color
        if param=="text_mode":
            return self.text_mode
        if param=="line_width":
            return self.width
        if param=="radius":
            return self.size
        if param=="width":
            return super().winfo_width()
        if param=="height":
            return super().winfo_height()
        if param=="values":
            return self.values
        if param=="text":
            raise ValueError(f"No such parameter: {param}")
        if param=="justify":
            raise ValueError(f"No such parameter: {param}")
        if param=="text_color":
            raise ValueError(f"No such parameter: {param}")
        if param=="text_color_disabled":
            raise ValueError(f"No such parameter: {param}")
        if param=="corner_radius":
            raise ValueError(f"No such parameter: {param}")
        if param=="font":
            raise ValueError(f"No such parameter: {param}")
        if param=="image":
            raise ValueError(f"No such parameter: {param}")
        
        return super().cget(param)

    def configure(self, **kwargs):
        if "bg_color" in kwargs:
            self.background = kwargs["bg_color"]
            kwargs.update({"fg_color": self.background})
        if "border_color" in kwargs:
            self.border_color = kwargs.pop("border_color")
        if "border_width" in kwargs:
            self.border_width = kwargs.pop("border_width")
        if "segment_border_width" in kwargs:
            self.segment_border_width = kwargs.pop("segment_border_width")
        if "segment_border_color" in kwargs:
            self.segment_border_color = kwargs.pop("segment_border_color")
        if "text_mode" in kwargs:
            self.text_mode = kwargs.pop("text_mode")
        if "radius" in kwargs:
            self.size = kwargs.pop("radius")
        if "values" in kwargs:
            self.values = kwargs.pop("values")
        if "line_width" in kwargs:
            self.width = kwargs.pop("line_width")
        
        super().configure(**kwargs)
        self.draw_pie_chart()

    def is_color_too_bright(self, hex_color, threshold=100):
        if not hex_color.startswith("#"): return False
        
        hex_color = hex_color.lstrip("#")
        r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        total = (r + g + b) / 3
        
        return True if total > threshold else False

    def get(self, tag=None):
        if tag:
            return self.values[tag]
        return self.values

    def change_text_mode(self, mode):
        """Change the text mode of the pie chart. Accepts 'percentage', 'custom', 'none', or 'toggle'."""
        if mode == "toggle":
            # Toggle between percentage and custom
            if self.text_mode == "percentage":
                self.text_mode = "custom"
            elif self.text_mode == "custom":
                self.text_mode = "percentage"
        elif mode in ("percentage", "custom", "none"):
            self.text_mode = mode
        else:
            raise ValueError(f"Invalid text mode: {mode}")
        self.draw_pie_chart()
